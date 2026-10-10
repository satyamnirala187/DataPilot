"""Create or update the read-only database role and store its connection string in .env.

Runs database/readonly_role.sql as the admin user (DATABASE_URL), sets a new random password
for datapilot_readonly, and writes READONLY_DATABASE_URL to the project's .env file.
Each run rotates the password. Neither URL nor the password is ever printed.

Run from the project root:
    .venv/bin/python database/create_readonly_role.py
"""

import secrets
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

from apply_schema import ENV_FILE, DatabaseSettings, connect

ROLE = "datapilot_readonly"
ROLE_FILE = Path(__file__).resolve().parent / "readonly_role.sql"
ENV_KEY = "READONLY_DATABASE_URL"


def readonly_url(admin_url: str, password: str) -> str:
    """Same host, port and database as the admin URL, but logging in as the read-only role."""
    parts = urlsplit(admin_url)
    # Supabase's pooler expects "<role>.<project-ref>"; a direct connection uses just "<role>".
    _, dot, project_ref = (parts.username or "").partition(".")
    username = f"{ROLE}.{project_ref}" if dot else ROLE
    netloc = f"{username}:{password}@{parts.hostname}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def write_env_value(key: str, value: str) -> None:
    """Set key=value in .env, replacing an existing line or appending a new one."""
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    lines = [line for line in lines if not line.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    admin_url = DatabaseSettings().database_url
    password = secrets.token_urlsafe(32)  # URL-safe characters only, so no escaping is needed
    try:
        with connect(admin_url) as conn, conn.transaction():
            conn.execute(ROLE_FILE.read_text(encoding="utf-8"))
            conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(ROLE), sql.Literal(password)))
    except psycopg.Error as error:
        # Never show the raw message: connection errors can include host and user names.
        raise SystemExit(f"Database error ({type(error).__name__}). Nothing was changed.") from None

    write_env_value(ENV_KEY, readonly_url(admin_url, password))
    print(f"Role {ROLE} configured; {ENV_KEY} written to .env")


if __name__ == "__main__":
    main()
