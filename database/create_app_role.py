"""Create or update the History and Saved Reports schema and its database role, and store the
role's connection string in .env.

Runs database/app_schema.sql (schema datapilot and its two tables) and then database/app_role.sql
(role datapilot_app and its grants) as the admin user (DATABASE_URL), in one transaction, sets a
new random password for datapilot_app, and writes APP_DATABASE_URL to the project's .env file.
Each run rotates the password. Nothing is dropped, so existing analyses and saved reports are kept.
Neither URL nor the password is ever printed.

Run from the project root:
    .venv/bin/python database/create_app_role.py
"""

import secrets
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
from psycopg import sql
from pydantic import ValidationError

from apply_schema import TLS_SSLMODES, DatabaseSettings, connect
from create_readonly_role import write_env_value

ROLE = "datapilot_app"
DATABASE_DIR = Path(__file__).resolve().parent
SQL_FILES = (DATABASE_DIR / "app_schema.sql", DATABASE_DIR / "app_role.sql")  # in this order
ENV_KEY = "APP_DATABASE_URL"


def app_url(admin_url: str, password: str) -> str:
    """Same host, port, database and options as the admin URL, but logging in as datapilot_app.

    The password is percent-encoded, so any character is safe in the URL. The URL itself also
    requires TLS: its sslmode is kept only if it already requires TLS, otherwise it becomes
    "require" (the app enforces the same rule again when it connects). Other options are kept as
    they are, byte for byte."""
    parts = urlsplit(admin_url)
    # Supabase's pooler expects "<role>.<project-ref>"; a direct connection uses just "<role>".
    _, dot, project_ref = (parts.username or "").partition(".")
    username = f"{ROLE}.{project_ref}" if dot else ROLE
    host = parts.hostname or ""
    host = f"[{host}]" if ":" in host else host  # an IPv6 address keeps its brackets
    netloc = f"{username}:{quote(password, safe='')}@{host}" + (f":{parts.port}" if parts.port else "")

    params = [param for param in parts.query.split("&") if param]
    mode = next((value for key, _, value in (p.partition("=") for p in params) if key == "sslmode"), None)
    params = [param for param in params if param.partition("=")[0] != "sslmode"]
    params.append(f"sslmode={mode if mode in TLS_SSLMODES else 'require'}")
    return urlunsplit((parts.scheme, netloc, parts.path, "&".join(params), parts.fragment))


def main() -> None:
    try:
        admin_url = DatabaseSettings().database_url
    except ValidationError:
        raise SystemExit("DATABASE_URL is not set in .env. Nothing was changed.") from None

    password = secrets.token_urlsafe(32)
    try:
        with connect(admin_url) as conn, conn.transaction():
            for path in SQL_FILES:
                conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(ROLE), sql.Literal(password)))
    except psycopg.errors.RaiseException as error:  # the ownership check in app_schema.sql
        raise SystemExit(f"{error.diag.message_primary} Nothing was changed.") from None
    except psycopg.Error as error:
        # Never show the raw message: connection errors can include host and user names.
        raise SystemExit(f"Database error ({type(error).__name__}). Nothing was changed.") from None

    write_env_value(ENV_KEY, app_url(admin_url, password))
    print(f"Schema datapilot and role {ROLE} configured; {ENV_KEY} written to .env")


if __name__ == "__main__":
    main()
