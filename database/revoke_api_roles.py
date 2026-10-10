"""Remove Supabase's Data API roles (anon, authenticated) from the six business tables.

Runs database/api_roles.sql as the admin user (DATABASE_URL), then checks that neither role has
any privilege left on the tables. Safe to re-run. The connection string is never printed.

Run from the project root:
    .venv/bin/python database/revoke_api_roles.py
"""

from pathlib import Path

import psycopg
from pydantic import ValidationError

from apply_schema import DatabaseSettings, connect

SQL_FILE = Path(__file__).resolve().parent / "api_roles.sql"
TABLES = ("customers", "categories", "products", "orders", "order_items", "payments")
API_ROLES = ("anon", "authenticated")
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


def remaining_grants(conn: psycopg.Connection) -> list[tuple[str, str, str]]:
    """(role, table, privilege) for every privilege an API role still has on the six tables."""
    roles = [row[0] for row in conn.execute("SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)", (list(API_ROLES),))]
    return [
        (role, table, privilege)
        for role in roles for table in TABLES for privilege in PRIVILEGES
        if conn.execute("SELECT has_table_privilege(%s, %s, %s)", (role, f"public.{table}", privilege)).fetchone()[0]
    ]


def main() -> None:
    try:
        admin_url = DatabaseSettings().database_url
    except ValidationError:
        raise SystemExit("DATABASE_URL is not set in .env. Nothing was changed.") from None

    try:
        with connect(admin_url, connect_timeout=10) as conn:
            with conn.transaction():
                conn.execute(SQL_FILE.read_text(encoding="utf-8"))
            left = remaining_grants(conn)
    except psycopg.errors.RaiseException as error:  # the ownership check in api_roles.sql
        raise SystemExit(f"{error.diag.message_primary} Nothing was changed.") from None
    except psycopg.Error as error:
        # Never show the raw message: connection errors can include host and user names.
        raise SystemExit(f"Database error ({type(error).__name__}). Nothing was changed.") from None

    if left:
        raise SystemExit(f"Revoked, but {len(left)} privileges remain, e.g. {left[0]}.")
    print("anon and authenticated have no privileges on the six tables.")


if __name__ == "__main__":
    main()
