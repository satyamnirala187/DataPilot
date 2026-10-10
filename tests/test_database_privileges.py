"""Database privilege checks: who can touch the six business tables.

Supabase's Data API roles (anon, authenticated) must have no access to the tables
(database/api_roles.sql), and the app's role must only read them (database/readonly_role.sql).

These connect as the read-only role and only inspect the catalog with PostgreSQL's privilege
functions: nothing is written and no role is switched. They are skipped when
READONLY_DATABASE_URL is not configured. A role that does not exist (a non-Supabase database)
has nothing to check.
"""

from pathlib import Path

import psycopg
import pytest

from app.config import settings
from app.db_executor import tls_sslmode
from app.sql_validator import ALLOWED_TABLES

TABLES = sorted(ALLOWED_TABLES)
API_ROLES = ("anon", "authenticated")
TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
API_ROLES_SQL = Path(__file__).resolve().parents[1] / "database" / "api_roles.sql"


def test_the_hardening_script_covers_every_table_the_app_can_query():
    sql = API_ROLES_SQL.read_text(encoding="utf-8")
    assert all(f"public.{table}" in sql for table in TABLES)
    assert all(f"'{role}'" in sql for role in API_ROLES)


# --- Against the real database -----------------------------------------------------------------

needs_database = pytest.mark.skipif(settings.readonly_database_url is None,
                                    reason="READONLY_DATABASE_URL is not configured")


@pytest.fixture(scope="module")
def catalog():
    try:
        url = settings.readonly_database_url.get_secret_value()
        conn = psycopg.connect(url, connect_timeout=5, sslmode=tls_sslmode(url))
    except psycopg.Error as error:
        pytest.fail(f"Could not connect as the read-only role ({type(error).__name__})", pytrace=False)
    conn.read_only = True

    def query(sql, *params):
        return conn.execute(sql, params).fetchall()

    yield query
    conn.rollback()
    conn.close()


def existing(catalog, roles):
    names = {row[0] for row in catalog("SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)", list(roles))}
    return [role for role in roles if role in names]


def granted(catalog, role, table, privilege):
    return catalog("SELECT has_table_privilege(%s, %s, %s)", role, f"public.{table}", privilege)[0][0]


@needs_database
@pytest.mark.parametrize("role", API_ROLES)
def test_supabase_api_roles_have_no_privileges_on_the_tables(catalog, role):
    if not existing(catalog, [role]):
        pytest.skip(f"no {role} role in this database")
    held = [(table, p) for table in TABLES for p in TABLE_PRIVILEGES if granted(catalog, role, table, p)]
    assert held == []


@needs_database
@pytest.mark.parametrize("role", API_ROLES)
def test_supabase_api_roles_cannot_use_the_tables_sequences(catalog, role):
    if not existing(catalog, [role]):
        pytest.skip(f"no {role} role in this database")
    held = catalog("""SELECT sequencename FROM pg_sequences WHERE schemaname = 'public'
                      AND (has_sequence_privilege(%s, 'public.' || sequencename, 'USAGE')
                           OR has_sequence_privilege(%s, 'public.' || sequencename, 'SELECT')
                           OR has_sequence_privilege(%s, 'public.' || sequencename, 'UPDATE'))""", role, role, role)
    assert held == []


@needs_database
def test_recreated_tables_would_not_be_granted_to_the_api_roles(catalog):
    # Default privileges of the tables' owner in public: what schema.sql's new tables would get.
    owners = catalog("SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY(%s)", TABLES)
    assert len(owners) == 1
    defaults = catalog("""SELECT d.defaclacl::text FROM pg_default_acl d
                          JOIN pg_namespace n ON n.oid = d.defaclnamespace
                          WHERE n.nspname = 'public' AND d.defaclobjtype IN ('r', 'S')
                            AND pg_get_userbyid(d.defaclrole) = %s""", owners[0][0])
    grantees = {entry.split("=")[0] for (acl,) in defaults for entry in acl.strip("{}").split(",")}
    assert grantees.isdisjoint(API_ROLES)


@needs_database
def test_the_app_role_can_only_read_the_tables(catalog):
    for table in TABLES:
        assert granted(catalog, "datapilot_readonly", table, "SELECT"), table
        assert not any(granted(catalog, "datapilot_readonly", table, p) for p in TABLE_PRIVILEGES[1:]), table
