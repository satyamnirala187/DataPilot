"""Tests for the History store (backend/app/history_store.py).

A fake psycopg connection only: nothing here reaches a database, and conftest's fake store is not
involved (these call the real record_analysis directly).
"""

import ast
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from app import history_store
from app.chart_selector import Visualization
from app.config import settings
from app.history_store import (ANALYSIS_EXISTS, DISABLED, GET_ANALYSIS, GET_SAVED_REPORT, INSERT_ANALYSIS,
                               LIST_HISTORY, LIST_SAVED_REPORTS, SAVE_REPORT, AlreadySaved, HistoryResult,
                               HistoryUnavailable, get_analysis, get_saved_report, list_history, list_saved_reports,
                               record_analysis, save_report)
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

    def fetchall(self):
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
    """Records statements; raises `error` on the statement number `fail_at` (0 = set_config, 1 = the
    first real statement). Statements through cursor() return the next of `results` (a row dict for
    fetchone, a list of them for fetchall); conn.execute() returns `row`."""

    def __init__(self, row=(NEW_ID,), error=None, fail_at=1, results=()):
        self.row, self.error, self.fail_at = row, error, fail_at
        self.results = list(results)
        self.statements, self.events, self.read_only = [], [], None

    def cursor(self, row_factory=None):
        assert row_factory is dict_row
        conn = self

        class Cursor:
            def execute(self, sql, params=None):
                conn.execute(sql, params)
                return FakeCursor(conn.results.pop(0))

        return Cursor()

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


# --- The History and Saved Reports API operations -------------------------------------------------
# Rows come back from the fake connection under the database's column names, so these also check
# the mapping to the API's names (generated_sql -> sql, result_columns -> columns, ...).

ANALYSIS_ID = UUID("0b6e8f2a-1c3d-4e5f-9a7b-8c9d0e1f2a3b")
REPORT_ID = UUID("5d4c3b2a-1f0e-4d9c-8b7a-6f5e4d3c2b1a")
CREATED = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)
SAVED = datetime(2026, 10, 2, 18, 0, tzinfo=UTC)

STORED_ROW = {
    "id": ANALYSIS_ID, "question": "Revenue by category?", "generated_sql": "SELECT c.name, 1 AS revenue FROM categories AS c",
    "result_columns": ["name", "revenue"], "result_rows": [["Books", 1], ["Toys", 2]], "row_count": 2,
    "truncated": False, "visualization": {"type": "bar", "x_key": "name", "y_key": "revenue"},
    "insight": "Toys lead.", "created_at": CREATED,
}
STORED = {
    "id": ANALYSIS_ID, "question": "Revenue by category?", "sql": "SELECT c.name, 1 AS revenue FROM categories AS c",
    "columns": ["name", "revenue"], "rows": [["Books", 1], ["Toys", 2]], "row_count": 2, "truncated": False,
    "visualization": {"type": "bar", "x_key": "name", "y_key": "revenue"}, "insight": "Toys lead.",
    "created_at": CREATED,
}
API_STATEMENTS = (LIST_HISTORY, GET_ANALYSIS, SAVE_REPORT, ANALYSIS_EXISTS, LIST_SAVED_REPORTS, GET_SAVED_REPORT)


def run(db, operation, *args, results=()):
    db.connection = FakeConnection(results=results)
    return operation(*args)


def api_statements(db):
    return db.connection.statements[1:]  # after set_config


def test_list_history_returns_summaries_newest_first_for_the_account(db):
    rows = [{"id": ANALYSIS_ID, "question": "Revenue by category?", "visualization_type": "bar", "row_count": 2,
             "truncated": False, "created_at": CREATED, "report_id": REPORT_ID},
            {"id": NEW_ID, "question": "Total orders?", "visualization_type": "kpi", "row_count": 1,
             "truncated": True, "created_at": CREATED, "report_id": None}]
    items = run(db, list_history, "demo", 50, results=[rows])
    assert [item.model_dump() for item in items] == [
        {"id": ANALYSIS_ID, "question": "Revenue by category?", "visualization_type": "bar", "row_count": 2,
         "truncated": False, "created_at": CREATED, "saved_report_id": REPORT_ID},
        {"id": NEW_ID, "question": "Total orders?", "visualization_type": "kpi", "row_count": 1,
         "truncated": True, "created_at": CREATED, "saved_report_id": None},
    ]
    assert api_statements(db) == [(LIST_HISTORY, ("demo", 50))]
    assert "ORDER BY a.created_at DESC" in LIST_HISTORY and "LEFT JOIN datapilot.saved_reports" in LIST_HISTORY
    assert "result_rows" not in LIST_HISTORY and "generated_sql" not in LIST_HISTORY  # summaries only


def test_get_analysis_maps_the_stored_snapshot_to_the_api_names(db):
    detail = run(db, get_analysis, "demo", ANALYSIS_ID, results=[STORED_ROW | {"report_id": None, "report_title": None}])
    assert detail.model_dump() == STORED | {"saved_report": None}
    assert api_statements(db) == [(GET_ANALYSIS, (ANALYSIS_ID, "demo"))]
    assert "WHERE a.id = %s AND a.account_id = %s" in GET_ANALYSIS


def test_get_analysis_includes_its_saved_report(db):
    row = STORED_ROW | {"report_id": REPORT_ID, "report_title": "Category revenue"}
    detail = run(db, get_analysis, "demo", ANALYSIS_ID, results=[row])
    assert detail.saved_report.model_dump() == {"id": REPORT_ID, "title": "Category revenue"}


def test_an_analysis_the_account_does_not_have_is_none(db):
    assert run(db, get_analysis, "demo", ANALYSIS_ID, results=[None]) is None


@pytest.mark.parametrize("bad", [
    {"visualization": {"type": "pie"}},  # not a chart DataPilot draws
    {"visualization": ["bar"]},
    {"result_columns": [1, 2]},
    {"result_rows": [{"name": "Books"}]},
    {"result_rows": "Books"},
    {"row_count": "many"},
])
def test_a_malformed_stored_record_is_refused_not_shown(db, bad):
    row = STORED_ROW | bad | {"report_id": None, "report_title": None}
    with pytest.raises(HistoryUnavailable) as caught:
        run(db, get_analysis, "demo", ANALYSIS_ID, results=[row])
    assert caught.value.kind == "invalid_record" and str(caught.value) == "invalid_record"


def test_save_report_inserts_only_for_the_accounts_analysis(db):
    inserted = {"report_id": REPORT_ID, "analysis_id": ANALYSIS_ID, "report_title": "Category revenue", "saved_at": SAVED}
    report = run(db, save_report, "demo", ANALYSIS_ID, "Category revenue", results=[inserted])
    assert report.model_dump() == {"id": REPORT_ID, "analysis_id": ANALYSIS_ID, "title": "Category revenue",
                                   "saved_at": SAVED}
    assert api_statements(db) == [(SAVE_REPORT, ("Category revenue", ANALYSIS_ID, "demo"))]
    assert "SELECT a.id, %s FROM datapilot.analyses AS a WHERE a.id = %s AND a.account_id = %s" in SAVE_REPORT
    assert "ON CONFLICT (analysis_id) DO NOTHING" in SAVE_REPORT
    assert db.connection.read_only is False and db.connection.events == ["begin", "commit", "close"]


def test_saving_an_already_saved_analysis_raises_already_saved(db):
    with pytest.raises(AlreadySaved):
        run(db, save_report, "demo", ANALYSIS_ID, "Again", results=[None, {"exists": True}])
    assert api_statements(db)[1] == (ANALYSIS_EXISTS, (ANALYSIS_ID, "demo"))


def test_saving_an_analysis_the_account_does_not_have_is_none(db):
    assert run(db, save_report, "demo", ANALYSIS_ID, "Mine?", results=[None, {"exists": False}]) is None


def test_list_saved_reports_returns_summaries_for_the_account(db):
    rows = [{"report_id": REPORT_ID, "report_title": "Category revenue", "saved_at": SAVED, "id": ANALYSIS_ID,
             "question": "Revenue by category?", "visualization_type": "bar", "row_count": 2, "truncated": False,
             "created_at": CREATED}]
    items = run(db, list_saved_reports, "demo", 10, results=[rows])
    assert [item.model_dump() for item in items] == [{
        "id": REPORT_ID, "analysis_id": ANALYSIS_ID, "title": "Category revenue", "question": "Revenue by category?",
        "visualization_type": "bar", "row_count": 2, "truncated": False, "saved_at": SAVED,
        "analysis_created_at": CREATED}]
    assert api_statements(db) == [(LIST_SAVED_REPORTS, ("demo", 10))]
    assert "ORDER BY r.saved_at DESC" in LIST_SAVED_REPORTS
    assert "result_rows" not in LIST_SAVED_REPORTS and "generated_sql" not in LIST_SAVED_REPORTS


def test_get_saved_report_returns_the_full_stored_analysis(db):
    row = STORED_ROW | {"report_id": REPORT_ID, "report_title": "Category revenue", "saved_at": SAVED}
    detail = run(db, get_saved_report, "demo", REPORT_ID, results=[row])
    assert detail.model_dump() == {"id": REPORT_ID, "title": "Category revenue", "saved_at": SAVED, "analysis": STORED}
    assert api_statements(db) == [(GET_SAVED_REPORT, (REPORT_ID, "demo"))]
    assert run(db, get_saved_report, "demo", REPORT_ID, results=[None]) is None


def test_reports_are_always_scoped_through_their_analysis():
    # saved_reports has no account column: every report query joins analyses and filters its account.
    for sql in (SAVE_REPORT, LIST_SAVED_REPORTS, GET_SAVED_REPORT):
        assert "datapilot.analyses AS a" in sql and "a.account_id = %s" in sql
    for sql in (LIST_HISTORY, GET_ANALYSIS, ANALYSIS_EXISTS):
        assert "account_id = %s" in sql


def test_api_statements_are_fixed_and_never_change_or_delete_data():
    for sql in API_STATEMENTS:
        assert re.findall(r"\b(UPDATE|DELETE|TRUNCATE|DROP|ALTER)\b", sql) == []
        assert "{" not in sql and "%(" not in sql  # fixed text; only %s parameters
        assert "public." not in sql and "datapilot." in sql


@pytest.mark.parametrize("operation, args", [
    (list_history, ("demo", 50)),
    (get_analysis, ("demo", ANALYSIS_ID)),
    (list_saved_reports, ("demo", 50)),
    (get_saved_report, ("demo", REPORT_ID)),
])
def test_reads_run_in_a_read_only_transaction(db, operation, args):
    run(db, operation, *args, results=[[] if operation in (list_history, list_saved_reports) else None])
    assert db.connection.read_only is True
    assert db.connection.statements[0] == ("SELECT set_config('statement_timeout', %s, true)", ("3000",))
    assert db.connects[0][0] == APP_URL and db.connects[0][1]["sslmode"] == "require"


OPERATIONS = [
    (list_history, ("demo", 50), "query_failed"),
    (get_analysis, ("demo", ANALYSIS_ID), "query_failed"),
    (save_report, ("demo", ANALYSIS_ID, "Title"), "insert_failed"),
    (list_saved_reports, ("demo", 50), "query_failed"),
    (get_saved_report, ("demo", REPORT_ID), "query_failed"),
]


@pytest.mark.parametrize("operation, args, refused_kind", OPERATIONS)
def test_api_operations_without_app_database_url_are_unavailable(db, monkeypatch, operation, args, refused_kind):
    monkeypatch.setattr(settings, "app_database_url", None)
    with pytest.raises(HistoryUnavailable) as caught:
        operation(*args)
    assert caught.value.kind == "disabled" and db.connects == []


@pytest.mark.parametrize("operation, args, refused_kind", OPERATIONS)
@pytest.mark.parametrize("error, kind", [
    (psycopg.OperationalError(f"server closed the connection: {APP_URL}"), "unavailable"),
    (psycopg.errors.QueryCanceled("canceling statement due to statement timeout"), "timeout"),
    (psycopg.errors.InsufficientPrivilege("permission denied for table analyses"), None),  # refused_kind
    (RuntimeError(f"unexpected, with {APP_URL} in it"), "RuntimeError"),
])
def test_api_operation_failures_are_safe(db, operation, args, refused_kind, error, kind):
    db.connection = FakeConnection(error=error)
    with pytest.raises(HistoryUnavailable) as caught:
        operation(*args)
    assert caught.value.kind == (kind or refused_kind)
    assert str(caught.value) == caught.value.kind and caught.value.__cause__ is None
    assert not any(secret in repr(caught.value) for secret in SECRETS)
    assert db.connection.events == ["begin", "rollback", "close"]


@pytest.mark.parametrize("operation, args, refused_kind", OPERATIONS)
def test_api_operations_cannot_connect_safely(db, operation, args, refused_kind):
    db.connection = psycopg.OperationalError(f"connection to server at app-db.example.test failed for {APP_URL}")
    with pytest.raises(HistoryUnavailable) as caught:
        operation(*args)
    assert caught.value.kind == "unavailable"
    assert not any(secret in repr(caught.value) for secret in SECRETS)
