"""Tests for the History and Saved Reports database foundation (Phase 19, batch 1).

database/app_schema.sql   schema datapilot, tables analyses and saved_reports
database/app_role.sql     role datapilot_app: SELECT and INSERT on those two tables only
database/create_app_role.py  runs both files and writes APP_DATABASE_URL to .env

These read the SQL files and run the setup script against a fake connection: nothing connects to a
database and nothing is written to the real .env. tests/test_database_privileges.py checks the real
grants once the schema exists.
"""

import importlib
import re
import sys
from contextlib import nullcontext
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from pydantic import SecretStr

from app import db_executor
from app.config import Settings
from app.db_executor import QueryExecutionError, execute_query

ROOT = Path(__file__).resolve().parents[1]
DATABASE_DIR = ROOT / "database"
BUSINESS_TABLES = ("customers", "categories", "products", "orders", "order_items", "payments")


def sql_text(name: str) -> str:
    """A SQL file without comments, with all whitespace collapsed to single spaces."""
    text = re.sub(r"--[^\n]*", "", (DATABASE_DIR / name).read_text(encoding="utf-8"))
    return " ".join(text.split())


APP_SCHEMA = sql_text("app_schema.sql")
APP_ROLE = sql_text("app_role.sql")


def table_definition(sql: str, table: str) -> dict[str, str]:
    """{column or constraint name: rest of its definition} for one CREATE TABLE in sql."""
    match = re.search(rf"CREATE TABLE IF NOT EXISTS {re.escape(table)} \((.*?)\);", sql)
    assert match, f"no CREATE TABLE IF NOT EXISTS {table}"
    items, depth, current = [], 0, ""
    for char in match.group(1):  # split on commas outside parentheses
        depth += {"(": 1, ")": -1}.get(char, 0)
        if char == "," and depth == 0:
            items.append(current.strip())
            current = ""
        else:
            current += char
    items.append(current.strip())
    return {item.split(" ", 1)[0]: item.split(" ", 1)[1] for item in items}


# --- Schema: database/app_schema.sql ----------------------------------------------------

def test_analyses_columns_and_constraints():
    assert table_definition(APP_SCHEMA, "datapilot.analyses") == {
        "id": "UUID PRIMARY KEY DEFAULT gen_random_uuid()",
        "account_id": "TEXT NOT NULL",
        "question": "TEXT NOT NULL CHECK (char_length(question) BETWEEN 1 AND 500)",
        "generated_sql": "TEXT NOT NULL CHECK (char_length(generated_sql) <= 20000)",
        "result_columns": "JSONB NOT NULL CHECK (jsonb_typeof(result_columns) = 'array')",
        "result_rows": "JSONB NOT NULL CHECK (jsonb_typeof(result_rows) = 'array')",
        "row_count": "INTEGER NOT NULL CHECK (row_count >= 0)",
        "truncated": "BOOLEAN NOT NULL",
        "visualization": "JSONB NOT NULL CHECK (jsonb_typeof(visualization) = 'object')",
        "insight": "TEXT CHECK (insight IS NULL OR char_length(insight) <= 600)",
        "snapshot_version": "SMALLINT NOT NULL DEFAULT 1",
        "created_at": "TIMESTAMPTZ NOT NULL DEFAULT now()",
    }


def test_history_is_indexed_by_account_newest_first():
    assert ("CREATE INDEX IF NOT EXISTS analyses_account_created_idx "
            "ON datapilot.analyses (account_id, created_at DESC);") in APP_SCHEMA


def test_saved_reports_reference_an_analysis_once_and_copy_nothing():
    assert table_definition(APP_SCHEMA, "datapilot.saved_reports") == {
        "id": "UUID PRIMARY KEY DEFAULT gen_random_uuid()",
        "analysis_id": "UUID NOT NULL REFERENCES datapilot.analyses (id) ON DELETE RESTRICT",
        "title": "TEXT NOT NULL CHECK (char_length(title) BETWEEN 1 AND 120)",
        "saved_at": "TIMESTAMPTZ NOT NULL DEFAULT now()",
        "UNIQUE": "(analysis_id)",  # one saved report per analysis in V1
    }


def test_every_object_is_created_only_if_missing_and_lives_in_schema_datapilot():
    creates = re.findall(r"CREATE (?:SCHEMA|TABLE|INDEX)[^(;]*", APP_SCHEMA)
    assert len(creates) == 4
    assert all("IF NOT EXISTS" in create for create in creates)
    assert "CREATE SCHEMA IF NOT EXISTS datapilot" in APP_SCHEMA
    assert re.findall(r"CREATE TABLE IF NOT EXISTS (\S+)", APP_SCHEMA) == ["datapilot.analyses", "datapilot.saved_reports"]


@pytest.mark.parametrize("sql", [APP_SCHEMA, APP_ROLE], ids=["app_schema.sql", "app_role.sql"])
def test_the_setup_files_never_drop_or_delete_anything(sql):
    # ON DELETE RESTRICT is a foreign-key rule, not a statement.
    statements = sql.replace("ON DELETE RESTRICT", "")
    assert re.findall(r"\b(DROP|TRUNCATE|DELETE|UPDATE)\b", statements, flags=re.IGNORECASE) == []


def test_reseeding_the_business_tables_never_touches_the_app_schema():
    schema = sql_text("schema.sql")
    assert "datapilot." not in schema.lower() and "create schema" not in schema.lower()
    dropped = re.search(r"DROP TABLE IF EXISTS ([^;]*);", schema).group(1)
    assert sorted(name.strip() for name in dropped.split(",")) == sorted(BUSINESS_TABLES)


def test_the_schema_belongs_to_the_admin_role_running_the_script():
    assert "pg_get_userbyid(nspowner) <> current_user" in APP_SCHEMA
    assert "tableowner <> current_user" in APP_SCHEMA
    assert "RAISE EXCEPTION" in APP_SCHEMA
    assert "OWNER TO" not in APP_SCHEMA + APP_ROLE  # never handed to datapilot_app


def test_public_supabase_roles_and_the_read_only_role_are_shut_out():
    for kind in ("SCHEMA datapilot", "ALL TABLES IN SCHEMA datapilot", "ALL SEQUENCES IN SCHEMA datapilot"):
        assert f"REVOKE ALL ON {kind} FROM PUBLIC;" in APP_SCHEMA
        assert f"EXECUTE format('REVOKE ALL ON {kind} FROM %I', other_role);" in APP_SCHEMA
    roles = re.search(r"FOREACH other_role IN ARRAY ARRAY\[([^\]]*)\]", APP_SCHEMA).group(1)
    assert [role.strip(" '") for role in roles.split(",")] == ["anon", "authenticated", "service_role",
                                                               "datapilot_readonly"]
    # Each is skipped only when the role does not exist (a database that is not Supabase).
    assert "CONTINUE WHEN NOT EXISTS (SELECT FROM pg_roles WHERE rolname = other_role);" in APP_SCHEMA
    assert "GRANT" not in APP_SCHEMA


def test_service_role_is_never_granted_anything():
    assert "service_role" not in APP_ROLE
    assert not re.search(r"GRANT[^;]*service_role", APP_SCHEMA + APP_ROLE)


def test_the_read_only_role_file_still_grants_nothing_outside_the_six_tables():
    readonly = sql_text("readonly_role.sql")
    assert "datapilot." not in readonly and "SCHEMA datapilot" not in readonly
    assert "GRANT SELECT ON customers, categories, products, orders, order_items, payments TO datapilot_readonly;" in readonly


# --- Role: database/app_role.sql --------------------------------------------------------

def test_app_role_has_no_special_powers():
    assert ("ALTER ROLE datapilot_app LOGIN NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT "
            "CONNECTION LIMIT 5;") in APP_ROLE
    assert "ALTER ROLE datapilot_app SET statement_timeout = '5s';" in APP_ROLE
    assert "ALTER ROLE datapilot_app SET idle_in_transaction_session_timeout = '15s';" in APP_ROLE


def test_app_role_starts_from_nothing_in_both_schemas():
    for schema in ("public", "datapilot"):
        assert f"REVOKE ALL ON ALL TABLES IN SCHEMA {schema} FROM datapilot_app;" in APP_ROLE
        assert f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {schema} FROM datapilot_app;" in APP_ROLE
        assert f"REVOKE ALL ON SCHEMA {schema} FROM datapilot_app;" in APP_ROLE
    # Every revoke comes before the first grant.
    assert APP_ROLE.rindex("REVOKE ALL") < APP_ROLE.index("GRANT")


def test_app_role_gets_exactly_select_and_insert_on_the_two_tables():
    assert re.findall(r"\bGRANT\b[^;']*", APP_ROLE) == [
        "GRANT CONNECT ON DATABASE %I TO datapilot_app",
        "GRANT USAGE ON SCHEMA datapilot TO datapilot_app",
        "GRANT SELECT, INSERT ON datapilot.analyses, datapilot.saved_reports TO datapilot_app",
    ]
    grants = " ".join(re.findall(r"\bGRANT\b[^;']*", APP_ROLE))
    for privilege in ("UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "CREATE", "ALL"):
        assert privilege not in grants
    assert not any(table in grants for table in BUSINESS_TABLES)


# --- Setup script: database/create_app_role.py ------------------------------------------

@pytest.fixture
def setup_script(monkeypatch):
    monkeypatch.syspath_prepend(str(DATABASE_DIR))
    module = importlib.import_module("create_app_role")
    yield module
    for name in ("create_app_role", "create_readonly_role", "apply_schema"):
        sys.modules.pop(name, None)


POOLER = "postgresql://postgres.abcdefgh:admin-pw@aws-0-region.pooler.supabase.com:5432/postgres"


def test_app_url_uses_the_pooler_user_name_and_keeps_host_port_and_database(setup_script):
    params = conninfo_to_dict(setup_script.app_url(POOLER + "?sslmode=require", "new-pw"))
    assert params == {"user": "datapilot_app.abcdefgh", "password": "new-pw", "host": "aws-0-region.pooler.supabase.com",
                      "port": "5432", "dbname": "postgres", "sslmode": "require"}


def test_app_url_for_a_direct_connection_uses_the_plain_role_name(setup_script):
    params = conninfo_to_dict(setup_script.app_url("postgresql://postgres:pw@db.example.test/postgres", "pw2"))
    assert (params["user"], params["host"], params["dbname"]) == ("datapilot_app", "db.example.test", "postgres")
    assert "port" not in params


def test_app_url_keeps_any_password_character_intact(setup_script):
    password = "p@ss:w/rd?#%&= +'\"é"
    url = setup_script.app_url(POOLER, password)
    assert conninfo_to_dict(url)["password"] == password
    assert password not in url  # percent-encoded, not raw


@pytest.mark.parametrize("query, expected", [
    ("", "require"),  # no sslmode: libpq would default to "prefer", which can fall back to plain text
    ("?sslmode=disable", "require"),
    ("?sslmode=allow", "require"),
    ("?sslmode=prefer", "require"),
    ("?sslmode=require", "require"),
    ("?sslmode=verify-ca", "verify-ca"),  # stricter modes are kept, never downgraded
    ("?sslmode=verify-full", "verify-full"),
])
def test_app_url_always_requires_tls(setup_script, query, expected):
    url = setup_script.app_url(POOLER + query, "pw")
    assert conninfo_to_dict(url)["sslmode"] == expected
    assert url.count("sslmode=") == 1


def test_app_url_keeps_other_options_as_they_are(setup_script):
    url = setup_script.app_url(POOLER + "?application_name=data%20pilot&sslmode=disable&connect_timeout=10", "pw")
    assert url.endswith("?application_name=data%20pilot&connect_timeout=10&sslmode=require")


def test_app_url_keeps_ipv6_brackets(setup_script):
    params = conninfo_to_dict(setup_script.app_url("postgresql://postgres:pw@[2001:db8::1]:6543/postgres", "pw"))
    assert (params["host"], params["port"]) == ("2001:db8::1", "6543")


class FakeConnection:
    """Records what the setup script runs. It never reaches a database."""

    def __init__(self, fail_with=None):
        self.statements, self.fail_with = [], fail_with

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def transaction(self):
        return nullcontext()

    def execute(self, statement):
        if self.fail_with:
            raise self.fail_with
        self.statements.append(statement if isinstance(statement, str) else statement.as_string(None))


@pytest.fixture
def fake_setup(setup_script, monkeypatch):
    """The script with a fake admin URL, a fake connection and a fake .env writer."""
    written, connections = {}, []

    class FakeSettings:
        database_url = POOLER + "?sslmode=require"

    def fake_connect(url, **kwargs):
        connections.append(FakeConnection())
        return connections[-1]

    monkeypatch.setattr(setup_script, "DatabaseSettings", FakeSettings)
    monkeypatch.setattr(setup_script, "connect", fake_connect)
    monkeypatch.setattr(setup_script, "write_env_value", lambda key, value: written.__setitem__(key, value))
    monkeypatch.setattr(setup_script.secrets, "token_urlsafe", lambda n: "generated-password-for-test")
    return setup_script, connections, written


def test_setup_runs_schema_then_role_then_sets_a_new_password_and_writes_the_url(fake_setup, capsys):
    script, connections, written = fake_setup
    script.main()

    (conn,) = connections
    assert conn.statements[:2] == [(DATABASE_DIR / "app_schema.sql").read_text(encoding="utf-8"),
                                   (DATABASE_DIR / "app_role.sql").read_text(encoding="utf-8")]
    assert conn.statements[2] == "ALTER ROLE \"datapilot_app\" PASSWORD 'generated-password-for-test'"
    assert list(written) == ["APP_DATABASE_URL"]
    params = conninfo_to_dict(written["APP_DATABASE_URL"])
    assert (params["user"], params["password"], params["sslmode"]) == ("datapilot_app.abcdefgh",
                                                                     "generated-password-for-test", "require")

    output = capsys.readouterr()
    assert output.out == "Schema datapilot and role datapilot_app configured; APP_DATABASE_URL written to .env\n"
    for secret in ("generated-password-for-test", "admin-pw", "pooler.supabase.com", "postgresql://"):
        assert secret not in output.out + output.err


def test_a_database_error_changes_nothing_and_reveals_nothing(fake_setup, monkeypatch, capsys):
    script, _, written = fake_setup
    error = psycopg.OperationalError("connection to server at aws-0-region.pooler.supabase.com failed: admin-pw")
    monkeypatch.setattr(script, "connect", lambda url, **kwargs: FakeConnection(fail_with=error))

    with pytest.raises(SystemExit) as caught:
        script.main()
    assert str(caught.value) == "Database error (OperationalError). Nothing was changed."
    assert written == {}
    assert "admin-pw" not in capsys.readouterr().out


def test_setup_without_database_url_stops_before_connecting(setup_script, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    real_settings = importlib.import_module("apply_schema").DatabaseSettings
    monkeypatch.setattr(setup_script, "DatabaseSettings", lambda: real_settings(_env_file=None))
    monkeypatch.setattr(setup_script, "connect", lambda *a, **k: pytest.fail("must not connect"))

    with pytest.raises(SystemExit, match="DATABASE_URL is not set"):
        setup_script.main()


def test_setup_connects_through_the_tls_helper(setup_script):
    # connect() is apply_schema's helper, which forces sslmode=require (tests/test_db_tls.py).
    assert setup_script.connect is importlib.import_module("apply_schema").connect
    assert "psycopg.connect(" not in (DATABASE_DIR / "create_app_role.py").read_text(encoding="utf-8")


# --- Runtime configuration --------------------------------------------------------------

def test_app_database_url_is_a_hidden_secret(monkeypatch):
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql://datapilot_app:hunter2@db.example.test/postgres")
    settings = Settings(_env_file=None)
    assert isinstance(settings.app_database_url, SecretStr)
    assert "hunter2" not in repr(settings) and "hunter2" not in str(settings.model_dump())
    assert settings.app_database_url.get_secret_value().endswith("@db.example.test/postgres")


def test_app_database_url_is_optional(monkeypatch):
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    assert Settings(_env_file=None).app_database_url is None


def test_the_runtime_still_has_no_admin_database_url():
    assert "database_url" not in Settings.model_fields
    assert {"readonly_database_url", "app_database_url"} <= set(Settings.model_fields)


def test_generated_sql_never_runs_with_the_app_url(monkeypatch):
    # Without READONLY_DATABASE_URL the executor refuses; it never falls back to APP_DATABASE_URL.
    monkeypatch.setattr(db_executor.settings, "readonly_database_url", None)
    monkeypatch.setattr(db_executor.settings, "app_database_url", SecretStr("postgresql://datapilot_app:pw@db.example.test/x"))
    monkeypatch.setattr(db_executor.psycopg, "connect", lambda *a, **k: pytest.fail("must not connect"))
    with pytest.raises(QueryExecutionError) as caught:
        execute_query("SELECT 1")
    assert caught.value.kind == "unavailable"


@pytest.mark.parametrize("module", ["db_executor.py", "query_service.py", "nl_to_sql.py", "sql_validator.py",
                                    "insight_service.py"])
def test_the_generated_sql_path_never_mentions_the_app_url(module):
    source = (ROOT / "backend" / "app" / module).read_text(encoding="utf-8")
    assert "app_database_url" not in source and "APP_DATABASE_URL" not in source
