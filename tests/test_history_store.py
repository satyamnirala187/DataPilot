"""Tests for the History store (backend/app/history_store.py).

A fake psycopg connection only: nothing here reaches a database, and conftest's fake store is not
involved (these call the real record_analysis directly).
"""

import ast
import logging
import re
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from app import history_store
from app.chart_selector import Visualization
from app.config import settings
from app.history_store import DISABLED, INSERT_ANALYSIS, HistoryResult, record_analysis
from app.query_service import QueryResponse

APP_URL = "postgresql://datapilot_app:app-secret-pw@app-db.example.test:5432/postgres"
READONLY_URL = "postgresql://datapilot_readonly:ro-secret-pw@ro-db.example.test:5432/postgres"
ADMIN_URL = "postgresql://postgres:admin-secret-pw@admin-db.example.test:5432/postgres"
SECRETS = ("app-secret-pw", "app-db.example.test", "datapilot_app", "postgresql://", "ro-secret-pw", "admin-secret-pw")
NEW_ID = UUID("6f1c2b9e-3d4a-4c5b-8e7f-0a1b2c3d4e5f")

# Everything a user can see in an answer, with characters that would break naive SQL building.
SNAPSHOT = QueryResponse(
    question="Which city's customers spend the most? \"quoted\"; DROP TABLE orders; --",
    sql="SELECT c.city, SUM(p.amount) AS revenue FROM customers AS c JOIN orders AS o ON o.customer_id = c.customer_id "
        "JOIN payments AS p ON p.order_id = o.order_id GROUP BY c.city LIMIT 501",
    columns=["city", "revenue"],
    rows=[["Pune", 1234.5], ["Mumbai's \"best\"", None], ["Delhi", 7]],
    row_count=3,
    truncated=True,
    visualization=Visualization(type="bar", x_key="city", y_key="revenue"),
    insight="Pune leads with ₹1,234.50.",
)
COLUMNS = re.search(r"\(([^)]*)\)\s*VALUES", INSERT_ANALYSIS).group(1).replace("\n", " ").split(",")
COLUMNS = [column.strip() for column in COLUMNS]


class FakeCursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeTransaction:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.events.append("begin")

    def __exit__(self, exc_type, *exc):
        self.conn.events.append("rollback" if exc_type else "commit")
        return False


class FakeConnection:
    """Records statements; raises `error` on the statement number `fail_at` (0 = set_config, 1 = INSERT)."""

    def __init__(self, row=(NEW_ID,), error=None, fail_at=1):
        self.row, self.error, self.fail_at = row, error, fail_at
        self.statements, self.events = [], []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.events.append("close")
        return False

    def transaction(self):
        return FakeTransaction(self)

    def execute(self, sql, params=None):
        if self.error is not None and len(self.statements) == self.fail_at:
            raise self.error
        self.statements.append((sql, params))
        return FakeCursor(self.row)


@pytest.fixture
def db(monkeypatch):
    """The real store with all three URLs configured, connecting to a fake connection."""
    state = type("State", (), {})()
    state.connection, state.connects = FakeConnection(), []

    def fake_connect(url, **kwargs):
        state.connects.append((url, kwargs))
        if isinstance(state.connection, Exception):
            raise state.connection
        return state.connection

    monkeypatch.setattr(settings, "app_database_url", SecretStr(APP_URL))
    monkeypatch.setattr(settings, "readonly_database_url", SecretStr(READONLY_URL))
    monkeypatch.setenv("DATABASE_URL", ADMIN_URL)
    monkeypatch.setattr(history_store.psycopg, "connect", fake_connect)
    return state


def bound_values(db):
    (_, params), (sql, values) = db.connection.statements
    assert sql == INSERT_ANALYSIS
    return dict(zip(COLUMNS, values, strict=True))


# --- Success --------------------------------------------------------------------------

def test_a_successful_insert_returns_the_new_analysis_id(db):
    assert record_analysis(SNAPSHOT, account_id="demo") == HistoryResult("saved", analysis_id=str(NEW_ID))


def test_it_inserts_into_datapilot_analyses_only(db):
    record_analysis(SNAPSHOT, account_id="demo")
    assert INSERT_ANALYSIS.split()[:3] == ["INSERT", "INTO", "datapilot.analyses"]
    assert INSERT_ANALYSIS.split()[-2:] == ["RETURNING", "id"]
    assert COLUMNS == ["account_id", "question", "generated_sql", "result_columns", "result_rows", "row_count",
                       "truncated", "visualization", "insight", "snapshot_version"]
    assert [sql for sql, _ in db.connection.statements][1] == INSERT_ANALYSIS


def test_every_field_the_user_saw_is_stored_exactly(db):
    record_analysis(SNAPSHOT, account_id="demo")
    values = bound_values(db)
    json_values = {name: values.pop(name) for name in ("result_columns", "result_rows", "visualization")}
    assert values == {
        "account_id": "demo",
        "question": SNAPSHOT.question,
        "generated_sql": SNAPSHOT.sql,
        "row_count": 3,
        "truncated": True,
        "insight": SNAPSHOT.insight,
        "snapshot_version": 1,
    }
    # JSON is bound as Jsonb parameters, never formatted into the SQL text.
    assert all(isinstance(value, Jsonb) for value in json_values.values())
    assert json_values["result_columns"].obj == ["city", "revenue"]
    assert json_values["result_rows"].obj == [["Pune", 1234.5], ["Mumbai's \"best\"", None], ["Delhi", 7]]
    assert json_values["visualization"].obj == {"type": "bar", "x_key": "city", "y_key": "revenue"}


def test_a_null_insight_and_an_empty_result_are_stored_as_they_are(db):
    empty = SNAPSHOT.model_copy(update={"rows": [], "row_count": 0, "truncated": False, "insight": None,
                                        "visualization": Visualization(type="table")})
    record_analysis(empty, account_id="demo")
    values = bound_values(db)
    assert (values["insight"], values["row_count"], values["result_rows"].obj) == (None, 0, [])
    assert values["visualization"].obj == {"type": "table", "x_key": None, "y_key": None}


def test_the_account_id_is_the_one_given(db):
    record_analysis(SNAPSHOT, account_id="another-account")
    assert bound_values(db)["account_id"] == "another-account"


def test_user_text_never_reaches_the_sql_text(db):
    record_analysis(SNAPSHOT, account_id="demo")
    for sql, _ in db.connection.statements:
        assert "DROP TABLE" not in sql and "Mumbai" not in sql and "Pune" not in sql


def test_one_short_transaction_with_a_statement_timeout(db):
    record_analysis(SNAPSHOT, account_id="demo")
    assert db.connection.statements[0] == ("SELECT set_config('statement_timeout', %s, true)", ("3000",))
    assert db.connection.events == ["begin", "commit", "close"]


# --- Which database, and how ------------------------------------------------------------

def test_it_connects_only_with_app_database_url(db):
    record_analysis(SNAPSHOT, account_id="demo")
    [(url, kwargs)] = db.connects
    assert url == APP_URL  # never READONLY_DATABASE_URL or the admin DATABASE_URL
    assert kwargs["connect_timeout"] == 5


@pytest.mark.parametrize("query, expected", [
    ("", "require"),
    ("?sslmode=disable", "require"),
    ("?sslmode=allow", "require"),
    ("?sslmode=prefer", "require"),
    ("?sslmode=require", "require"),
    ("?sslmode=verify-ca", "verify-ca"),
    ("?sslmode=verify-full", "verify-full"),
])
def test_tls_is_always_required(db, monkeypatch, query, expected):
    monkeypatch.setattr(settings, "app_database_url", SecretStr(APP_URL + query))
    record_analysis(SNAPSHOT, account_id="demo")
    assert db.connects[0][1]["sslmode"] == expected


def test_without_app_database_url_history_is_disabled_and_nothing_connects(db, monkeypatch):
    monkeypatch.setattr(settings, "app_database_url", None)
    assert record_analysis(SNAPSHOT, account_id="demo") == DISABLED == HistoryResult("disabled")
    assert db.connects == []


def test_the_store_never_reads_another_url_or_runs_generated_sql():
    # Checked on the code itself (not its docstring): no read-only or admin URL, no executor.
    tree = ast.parse(Path(history_store.__file__).read_text(encoding="utf-8"))
    names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
    assert "app_database_url" in names
    assert not names & {"readonly_database_url", "database_url", "execute_query", "_readonly_url", "environ", "getenv"}


# --- Failures: never raised, never revealing ----------------------------------------------

def test_a_malformed_url_is_a_safe_failure(db, monkeypatch):
    monkeypatch.setattr(settings, "app_database_url", SecretStr("postgresql://u:app-secret-pw@h/db?bad"))
    assert record_analysis(SNAPSHOT, account_id="demo") == HistoryResult("failed", error="invalid_configuration")
    assert db.connects == []


def test_a_connection_failure_is_a_safe_failure(db, caplog):
    db.connection = psycopg.OperationalError(f"connection to server at app-db.example.test failed for {APP_URL}")
    result = record_analysis(SNAPSHOT, account_id="demo")
    assert result == HistoryResult("failed", error="unavailable")
    assert not any(secret in repr(result) for secret in SECRETS)
    assert caplog.records == []  # the store logs nothing itself; /query logs the outcome


@pytest.mark.parametrize("error, kind", [
    (psycopg.errors.CheckViolation("new row violates check constraint"), "insert_failed"),
    (psycopg.errors.InsufficientPrivilege("permission denied for table analyses"), "insert_failed"),
    (psycopg.errors.QueryCanceled("canceling statement due to statement timeout"), "timeout"),
    (psycopg.OperationalError(f"server closed the connection: {APP_URL}"), "unavailable"),
    (psycopg.DataError("invalid input"), "insert_failed"),
    (RuntimeError(f"unexpected, with {APP_URL} in it"), "RuntimeError"),
    (TypeError("not JSON serialisable: app-secret-pw"), "TypeError"),
])
def test_insert_failures_are_safe_and_never_raised(db, error, kind):
    db.connection = FakeConnection(error=error)
    result = record_analysis(SNAPSHOT, account_id="demo")
    assert result == HistoryResult("failed", error=kind)
    assert not any(secret in repr(result) for secret in SECRETS)
    assert db.connection.events == ["begin", "rollback", "close"]  # nothing half-written


def test_a_failure_while_setting_the_timeout_is_safe(db):
    db.connection = FakeConnection(error=psycopg.OperationalError("lost"), fail_at=0)
    assert record_analysis(SNAPSHOT, account_id="demo") == HistoryResult("failed", error="unavailable")


def test_an_insert_that_returns_nothing_is_a_safe_failure(db):
    db.connection = FakeConnection(row=None)
    assert record_analysis(SNAPSHOT, account_id="demo") == HistoryResult("failed", error="TypeError")


def test_a_broken_snapshot_is_a_safe_failure(db):
    broken = SNAPSHOT.model_copy(update={"visualization": None})
    assert record_analysis(broken, account_id="demo") == HistoryResult("failed", error="AttributeError")


def test_the_store_logs_nothing(db, caplog):
    caplog.set_level(logging.DEBUG)
    record_analysis(SNAPSHOT, account_id="demo")
    assert [r for r in caplog.records if r.name.startswith("app")] == []
