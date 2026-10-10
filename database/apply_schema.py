"""Apply database/schema.sql to the database in DATABASE_URL.

WARNING: schema.sql drops and recreates all six tables, so existing data is deleted.

Run from the project root:
    .venv/bin/python database/apply_schema.py
"""

from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict
from pydantic_settings import BaseSettings, SettingsConfigDict

DATABASE_DIR = Path(__file__).resolve().parent
ENV_FILE = DATABASE_DIR.parent / ".env"
SCHEMA_FILE = DATABASE_DIR / "schema.sql"
# Same rule as the app (backend/app/db_executor.py): never connect without TLS.
TLS_SSLMODES = ("require", "verify-ca", "verify-full")


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    # Admin connection, used only by setup scripts. Never printed.
    database_url: str


def connect(url: str, **kwargs) -> psycopg.Connection:
    """Connect as the admin user with TLS required: the URL's sslmode is kept only if it already
    requires TLS, otherwise "require" is used. Shared by every setup script in database/."""
    mode = conninfo_to_dict(url).get("sslmode")
    return psycopg.connect(url, sslmode=mode if mode in TLS_SSLMODES else "require", **kwargs)


def main() -> None:
    settings = DatabaseSettings()
    # autocommit, because schema.sql manages its own BEGIN/COMMIT transaction.
    with connect(settings.database_url, autocommit=True) as conn:
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
    print(f"Applied {SCHEMA_FILE.name}")


if __name__ == "__main__":
    main()
