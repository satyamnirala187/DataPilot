"""Apply database/schema.sql to the database in DATABASE_URL.

WARNING: schema.sql drops and recreates all six tables, so existing data is deleted.

Run from the project root:
    .venv/bin/python database/apply_schema.py
"""

from pathlib import Path

import psycopg
from pydantic_settings import BaseSettings, SettingsConfigDict

DATABASE_DIR = Path(__file__).resolve().parent
ENV_FILE = DATABASE_DIR.parent / ".env"
SCHEMA_FILE = DATABASE_DIR / "schema.sql"


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    # Admin connection, used only by setup scripts. Never printed.
    database_url: str


def main() -> None:
    settings = DatabaseSettings()
    # autocommit, because schema.sql manages its own BEGIN/COMMIT transaction.
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
    print(f"Applied {SCHEMA_FILE.name}")


if __name__ == "__main__":
    main()
