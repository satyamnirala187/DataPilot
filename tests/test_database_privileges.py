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


# --- History and Saved Reports: schema datapilot (database/app_schema.sql, app_role.sql) -------
# Skipped until database/create_app_role.py has been run against this database. Objects are looked
# up by OID: datapilot_readonly has no USAGE on schema datapilot, so it cannot even resolve the
# tables' names, which is the point.

APP_SCHEMA = "datapilot"
APP_TABLES = ("analyses", "saved_reports")
APP_ROLE = "datapilot_app"
SCHEMA_PRIVILEGES = ("USAGE", "CREATE")


@pytest.fixture(scope="module")
def app_schema(catalog):
    found = catalog("SELECT oid FROM pg_namespace WHERE nspname = %s", APP_SCHEMA)
    if not found:
        pytest.skip("schema datapilot does not exist yet (database/create_app_role.py)")
    schema_oid = found[0][0]
    tables = dict(catalog("SELECT relname, oid FROM pg_class WHERE relnamespace = %s AND relkind IN ('r', 'p')", schema_oid))
    return schema_oid, tables


@pytest.fixture(scope="module")
def app_role(catalog, app_schema):
    if not existing(catalog, [APP_ROLE]):
        pytest.skip(f"no {APP_ROLE} role yet (database/create_app_role.py)")
    return APP_ROLE


def schema_privileges(catalog, role, schema_oid):
    return {p for p in SCHEMA_PRIVILEGES if catalog("SELECT has_schema_privilege(%s, %s::oid, %s)", role, schema_oid, p)[0][0]}


def table_privileges(catalog, role, table_oid):
    return {p for p in TABLE_PRIVILEGES if catalog("SELECT has_table_privilege(%s, %s::oid, %s)", role, table_oid, p)[0][0]}


def any_column_privilege(catalog, role, table_oid):
    return catalog("SELECT has_any_column_privilege(%s, %s::oid, 'SELECT, INSERT, UPDATE, REFERENCES')",
                   role, table_oid)[0][0]


@needs_database
def test_the_app_schema_holds_exactly_the_two_tables(app_schema):
    _, tables = app_schema
    assert sorted(tables) == sorted(APP_TABLES)


@needs_database
def test_the_read_only_role_has_no_access_to_the_app_schema(catalog, app_schema):
    schema_oid, tables = app_schema
    assert schema_privileges(catalog, "datapilot_readonly", schema_oid) == set()
    for table, oid in tables.items():
        assert table_privileges(catalog, "datapilot_readonly", oid) == set(), table
        assert not any_column_privilege(catalog, "datapilot_readonly", oid), table


@needs_database
@pytest.mark.parametrize("role", [*API_ROLES, "service_role", "public"])
def test_public_and_supabase_api_roles_have_no_access_to_the_app_schema(catalog, app_schema, role):
    if role != "public" and not existing(catalog, [role]):
        pytest.skip(f"no {role} role in this database")
    schema_oid, tables = app_schema
    assert schema_privileges(catalog, role, schema_oid) == set()  # neither USAGE nor CREATE
    for table, oid in tables.items():
        assert table_privileges(catalog, role, oid) == set(), table
        assert not any_column_privilege(catalog, role, oid), table


@needs_database
def test_the_app_role_can_only_select_and_insert_history_and_reports(catalog, app_schema, app_role):
    schema_oid, tables = app_schema
    assert schema_privileges(catalog, app_role, schema_oid) == {"USAGE"}  # no CREATE
    for table, oid in tables.items():
        assert table_privileges(catalog, app_role, oid) == {"SELECT", "INSERT"}, table  # no UPDATE, DELETE, TRUNCATE


@needs_database
def test_the_app_role_may_connect_but_not_create_in_the_database(catalog, app_role):
    assert catalog("SELECT has_database_privilege(%s, current_database(), 'CONNECT')", app_role)[0][0]
    assert not catalog("SELECT has_database_privilege(%s, current_database(), 'CREATE')", app_role)[0][0]


@needs_database
def test_saved_reports_reference_analyses_once_and_history_is_indexed(catalog, app_schema):
    _, tables = app_schema
    constraints = catalog("""SELECT contype, pg_get_constraintdef(oid) FROM pg_constraint
                             WHERE conrelid = %s::oid AND contype IN ('f', 'u') ORDER BY contype""",
                          tables["saved_reports"])
    assert constraints == [("f", "FOREIGN KEY (analysis_id) REFERENCES datapilot.analyses(id) ON DELETE RESTRICT"),
                           ("u", "UNIQUE (analysis_id)")]
    indexes = [row[0] for row in catalog("SELECT pg_get_indexdef(indexrelid) FROM pg_index WHERE indrelid = %s::oid",
                                         tables["analyses"])]
    assert ("CREATE INDEX analyses_account_created_idx ON datapilot.analyses "
            "USING btree (account_id, created_at DESC)") in indexes


@needs_database
def test_the_app_role_has_no_access_to_the_business_tables(catalog, app_role):
    for table in TABLES:
        assert not any(granted(catalog, app_role, table, p) for p in TABLE_PRIVILEGES), table
        assert not catalog("SELECT has_any_column_privilege(%s, %s, 'SELECT, INSERT, UPDATE, REFERENCES')",
                           app_role, f"public.{table}")[0][0], table
    held = catalog("""SELECT sequencename FROM pg_sequences WHERE schemaname = 'public'
                      AND (has_sequence_privilege(%s, 'public.' || sequencename, 'USAGE')
                           OR has_sequence_privilege(%s, 'public.' || sequencename, 'SELECT')
                           OR has_sequence_privilege(%s, 'public.' || sequencename, 'UPDATE'))""",
                   app_role, app_role, app_role)
    assert held == []


@needs_database
def test_the_app_role_has_no_special_powers(catalog, app_role):
    (role,) = catalog("""SELECT rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls,
                                rolcanlogin, rolconnlimit, rolconfig, oid
                         FROM pg_roles WHERE rolname = %s""", app_role)
    assert role[:8] == (False, False, False, False, False, False, True, 5)
    assert {"statement_timeout=5s", "idle_in_transaction_session_timeout=15s"} <= set(role[8] or [])
    assert catalog("SELECT roleid FROM pg_auth_members WHERE member = %s", role[9]) == []  # a member of no role


@needs_database
def test_the_app_schema_belongs_to_the_owner_of_the_business_tables(catalog, app_schema):
    schema_oid, tables = app_schema
    (business_owner,) = {row[0] for row in catalog(
        "SELECT tableowner FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY(%s)", TABLES)}
    owners = {row[0] for row in catalog("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE oid = %s", schema_oid)}
    owners |= {row[0] for row in catalog("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = ANY(%s)",
                                         list(tables.values()))}
    assert owners == {business_owner} and APP_ROLE not in owners


needs_app_database = pytest.mark.skipif(settings.app_database_url is None, reason="APP_DATABASE_URL is not configured")


@needs_app_database
def test_the_app_connection_is_encrypted_and_cannot_read_business_data():
    url = settings.app_database_url.get_secret_value()
    try:
        conn = psycopg.connect(url, connect_timeout=5, sslmode=tls_sslmode(url))
    except psycopg.Error as error:
        pytest.fail(f"Could not connect as the app role ({type(error).__name__})", pytrace=False)
    conn.read_only = True
    try:
        assert conn.pgconn.ssl_in_use
        assert conn.execute("SELECT current_user").fetchone()[0] == APP_ROLE
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT 1 FROM public.orders LIMIT 1")
    finally:
        conn.rollback()
        conn.close()
