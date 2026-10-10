"""Tests for History's in-process connection cap (backend/app/history_store.py, ConnectionSlots).

datapilot_app may hold at most 5 connections. The History and Saved Reports API may use 4 of them, so
recording a /query answer always has one; nobody waits for ever. A fake psycopg.connect counts the
connections that are open at once: nothing here reaches a database or Gemini, and nothing sleeps
for the real waits (the tests shorten them).
"""

import logging
import re
import threading
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import history_store, main
from app.chart_selector import Visualization
from app.config import settings
from app.db_executor import QueryResult
from app.history_store import (ConnectionSlots, HistoryResult, HistoryUnavailable, get_analysis, get_saved_report,
                               list_history, list_saved_reports, record_analysis, save_report)
from app.query_service import QueryResponse, run_business_query
from app.rate_limiter import RateLimiter

ROOT = Path(__file__).resolve().parents[1]
APP_URL = "postgresql://datapilot_app:app-secret-pw@app-db.example.test:5432/postgres"
SECRETS = ("app-secret-pw", "app-db.example.test", "datapilot_app", "postgresql://")
NEW_ID = UUID("6f1c2b9e-3d4a-4c5b-8e7f-0a1b2c3d4e5f")
ANALYSIS_ID = UUID("0b6e8f2a-1c3d-4e5f-9a7b-8c9d0e1f2a3b")
SNAPSHOT = QueryResponse(question="Total revenue?", sql="SELECT 1 AS revenue", columns=["revenue"], rows=[[1]],
                         row_count=1, truncated=False, visualization=Visualization(type="kpi", y_key="revenue"),
                         insight=None)
SHORT_WAIT = 0.05  # seconds; long enough to be a real wait, short enough to keep the tests fast

API_OPERATIONS = [
    (list_history, ("demo", 50)),
    (get_analysis, ("demo", ANALYSIS_ID)),
    (save_report, ("demo", ANALYSIS_ID, "Title")),
    (list_saved_reports, ("demo", 50)),
    (get_saved_report, ("demo", ANALYSIS_ID)),
]


class Database:
    """Stands in for psycopg.connect and counts the connections open at once. A connection opened
    from a thread named "hold-..." stays open (inside its first statement) until hold() ends."""

    def __init__(self):
        self.lock = threading.Lock()
        self.open = self.peak = self.connects = 0
        self.opened = threading.Semaphore(0)  # one permit per connection opened
        self.release = threading.Event()
        self.connect_error = self.statement_error = None
        self.rows = []  # what fetchall() returns

    def connect(self, url, **kwargs):
        if self.connect_error is not None:
            raise self.connect_error
        with self.lock:
            self.connects += 1
            self.open += 1
            self.peak = max(self.peak, self.open)
        self.opened.release()
        return Connection(self, hold=threading.current_thread().name.startswith("hold"))

    def closed(self):
        with self.lock:
            self.open -= 1

    def wait_until_open(self, count):
        """Wait until `count` more connections have been opened (each opened connection counts once)."""
        for _ in range(count):
            assert self.opened.acquire(timeout=5), "a connection was expected to open"

    @contextmanager
    def hold(self, *operations):
        """Run each operation in its own "hold-" thread; their connections stay open until the block
        ends. Yields the list of their results (a return value or the exception raised)."""
        self.release = threading.Event()
        results = [None] * len(operations)

        def run(index, operation):
            try:
                results[index] = operation()
            except Exception as error:
                results[index] = error

        threads = [threading.Thread(target=run, args=(i, op), name=f"hold-{i}", daemon=True)
                   for i, op in enumerate(operations)]
        for thread in threads:
            thread.start()
        try:
            yield results
        finally:
            self.release.set()
            for thread in threads:
                thread.join(timeout=5)
            assert not any(thread.is_alive() for thread in threads)


class Connection:
    def __init__(self, db, hold):
        self.db, self.release, self.read_only = db, db.release if hold else None, None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.db.closed()
        return False

    def transaction(self):
        return nullcontext()

    def cursor(self, row_factory=None):
        return self

    def execute(self, sql, params=None):
        if self.release is not None:
            assert self.release.wait(timeout=5), "a held connection was never released"
            self.release = None
        if self.db.statement_error is not None and "set_config" not in sql:
            raise self.db.statement_error
        return self

    def fetchone(self):
        return (NEW_ID,)

    def fetchall(self):
        return self.db.rows


@pytest.fixture
def db(monkeypatch):
    """The real store with fresh slots, short waits and a fake database."""
    database = Database()
    monkeypatch.setattr(settings, "app_database_url", SecretStr(APP_URL))
    monkeypatch.setattr(history_store.psycopg, "connect", database.connect)
    monkeypatch.setattr(history_store, "slots", ConnectionSlots())
    monkeypatch.setattr(history_store, "API_SLOT_WAIT_SECONDS", SHORT_WAIT)
    monkeypatch.setattr(history_store, "RECORD_SLOT_WAIT_SECONDS", SHORT_WAIT)
    return database


def api_call():
    return list_history("demo", 50)


def record_call():
    return record_analysis(SNAPSHOT, account_id="demo")


def assert_busy(operation, *args):
    with pytest.raises(HistoryUnavailable) as caught:
        operation(*args)
    assert caught.value.kind == "busy" and str(caught.value) == "busy" and caught.value.__cause__ is None


def assert_full_capacity(db):
    """All five slots can be taken again (four by the API, one by recording), and no more."""
    db.statement_error = db.connect_error = None
    db.rows = []
    with db.hold(api_call, api_call, api_call, api_call, record_call) as results:
        db.wait_until_open(5)
        assert db.open == 5
        assert_busy(api_call)
        assert record_call() == HistoryResult("failed", error="busy")
    assert results[:4] == [[]] * 4 and results[4].status == "saved"


# --- The limits ------------------------------------------------------------------------------------

def test_the_limits_match_the_role_and_the_single_worker():
    role_limit = re.search(r"CONNECTION LIMIT (\d+)", (ROOT / "database/app_role.sql").read_text()).group(1)
    assert history_store.MAX_CONNECTIONS == int(role_limit) == 5
    assert history_store.MAX_API_CONNECTIONS == 4
    assert history_store.API_SLOT_WAIT_SECONDS == 5.0
    assert 0 < history_store.RECORD_SLOT_WAIT_SECONDS <= 2.0
    assert "--workers 1" in (ROOT / "render.yaml").read_text()  # the cap is per process


def test_at_most_four_api_operations_hold_connections_and_the_rest_wait_their_turn(db, monkeypatch):
    monkeypatch.setattr(history_store, "API_SLOT_WAIT_SECONDS", 5.0)
    with db.hold(*[api_call] * 6) as results:
        db.wait_until_open(4)
        assert not db.opened.acquire(timeout=0.2)  # the fifth and sixth wait for a slot
        assert db.open == 4
    assert results == [[]] * 6  # all six succeed once slots are given back
    assert db.peak == 4 and db.connects == 6 and db.open == 0


@pytest.mark.parametrize("operation, args", API_OPERATIONS)
def test_a_fifth_api_operation_is_busy_and_never_connects(db, operation, args):
    with db.hold(*[api_call] * 4):
        db.wait_until_open(4)
        assert_busy(operation, *args)
        assert db.connects == 4
    assert db.peak == 4


def test_recording_gets_the_fifth_connection_while_the_api_holds_four(db):
    with db.hold(*[api_call] * 4):
        db.wait_until_open(4)
        assert record_call() == HistoryResult("saved", analysis_id=str(NEW_ID))
    assert db.peak == 5


@pytest.mark.parametrize("api_holders, record_holders", [(4, 1), (3, 2), (0, 5)])
def test_never_more_than_five_connections(db, api_holders, record_holders):
    with db.hold(*[api_call] * api_holders, *[record_call] * record_holders):
        db.wait_until_open(5)
        assert record_call() == HistoryResult("failed", error="busy")
        assert_busy(api_call)
        assert db.open == 5 and db.connects == 5
    assert db.peak == 5


def test_a_disabled_store_never_waits_for_a_slot(db, monkeypatch):
    monkeypatch.setattr(history_store, "API_SLOT_WAIT_SECONDS", 5.0)
    with db.hold(*[api_call] * 4):
        db.wait_until_open(4)
        monkeypatch.setattr(settings, "app_database_url", None)
        with pytest.raises(HistoryUnavailable) as caught:
            api_call()
        assert caught.value.kind == "disabled"
        monkeypatch.setattr(settings, "app_database_url", SecretStr(APP_URL))


# --- A slot is taken before connecting and given back after closing, whatever happens ----------------

class RecordingSlots(ConnectionSlots):
    def __init__(self, events):
        super().__init__()
        self.events = events

    @contextmanager
    def hold(self, **kwargs):
        with super().hold(**kwargs):
            self.events.append("slot taken")
            try:
                yield
            finally:
                self.events.append("slot given back")


@pytest.mark.parametrize("operation", [api_call, record_call])
def test_the_slot_covers_the_whole_life_of_the_connection(db, monkeypatch, operation):
    events = []
    monkeypatch.setattr(history_store, "slots", RecordingSlots(events))
    connect, closed = db.connect, db.closed
    monkeypatch.setattr(history_store.psycopg, "connect", lambda *a, **k: events.append("connect") or connect(*a, **k))
    monkeypatch.setattr(db, "closed", lambda: events.append("close") or closed())
    operation()
    assert events == ["slot taken", "connect", "close", "slot given back"]


MALFORMED_ROW = {"id": "not-a-uuid", "question": None, "visualization_type": "pie", "row_count": -1,
                 "truncated": None, "created_at": None, "report_id": None}


@pytest.mark.parametrize("failure, api_kind, record_error", [
    ("connect", "unavailable", "unavailable"),
    (psycopg.errors.QueryCanceled("canceling statement due to statement timeout"), "timeout", "timeout"),
    (psycopg.errors.InsufficientPrivilege("permission denied"), "query_failed", "insert_failed"),
    (RuntimeError("unexpected"), "RuntimeError", "RuntimeError"),
    ("malformed_record", "invalid_record", None),
])
def test_failures_give_their_slot_back(db, failure, api_kind, record_error):
    if failure == "connect":
        db.connect_error = psycopg.OperationalError(f"connection failed: {APP_URL}")
    elif failure == "malformed_record":
        db.rows = [MALFORMED_ROW]
    else:
        db.statement_error = failure
    for _ in range(history_store.MAX_CONNECTIONS + 2):  # more failures than there are slots
        with pytest.raises(HistoryUnavailable) as caught:
            api_call()
        assert caught.value.kind == api_kind
        if record_error is not None:
            assert record_call() == HistoryResult("failed", error=record_error)
    assert db.open == 0
    assert_full_capacity(db)


def test_a_busy_api_operation_gives_back_the_api_slot_it_took(db):
    # Recording holds all five connections: an API operation gets an API slot, then finds no
    # connection free. The API slot must not stay taken.
    with db.hold(*[record_call] * 5):
        db.wait_until_open(5)
        for _ in range(history_store.MAX_API_CONNECTIONS + 2):
            assert_busy(api_call)
    assert_full_capacity(db)


def test_an_exception_inside_the_slot_gives_it_back():
    slots = ConnectionSlots(total=1, api=1)
    with pytest.raises(KeyError), slots.hold(api=True, wait_seconds=SHORT_WAIT):
        raise KeyError("boom")
    with slots.hold(api=True, wait_seconds=SHORT_WAIT):  # the one slot is free again
        pass


# --- Through the API ---------------------------------------------------------------------------------

def test_a_busy_api_request_is_the_existing_safe_503(db, monkeypatch, caplog):
    monkeypatch.setattr(main, "list_history", history_store.list_history)  # the real store, not conftest's fake
    monkeypatch.setattr(main, "app_data_limiter", RateLimiter(max_requests=100, window_seconds=60))
    caplog.set_level(logging.DEBUG)
    with db.hold(*[api_call] * 4):
        db.wait_until_open(4)
        response = TestClient(main.app, raise_server_exceptions=False).get("/history")
    assert response.status_code == 503
    assert response.json() == {"error": {"code": "history_unavailable",
                                         "message": "History is not available right now. Please try again later."}}
    [line] = [r.getMessage() for r in caplog.records if "event=history_unavailable" in r.getMessage()]
    assert line.endswith("endpoint=history cause=busy")
    for secret in SECRETS:
        assert secret not in response.text and secret not in caplog.text


def test_a_query_whose_history_is_busy_still_returns_the_answer_once(db, monkeypatch, caplog):
    calls = {"generate": 0, "execute": 0}

    def generate(question):
        calls["generate"] += 1
        return "SELECT c.name AS category, 7 AS revenue FROM categories AS c"

    def execute(sql):
        calls["execute"] += 1
        return QueryResult(columns=["category", "revenue"], rows=[["Books", 7]], truncated=False)

    monkeypatch.setattr(main, "record_analysis", history_store.record_analysis)
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(max_requests=100, window_seconds=60))
    monkeypatch.setattr(main, "run_business_query", lambda q, **kw: run_business_query(
        q, generate=generate, execute=execute, summarize=lambda *a: "Books lead.", sleep=lambda s: None, **kw))
    caplog.set_level(logging.INFO)
    with db.hold(*[api_call] * 4, record_call):  # all five connections taken
        db.wait_until_open(5)
        response = TestClient(main.app, raise_server_exceptions=False).post("/query", json={"question": "Revenue?"})
    assert response.status_code == 200
    body = response.json()
    assert body["analysis_id"] is None and body["rows"] == [["Books", 7]] and body["insight"] == "Books lead."
    assert calls == {"generate": 1, "execute": 1}  # no Gemini retry, no SQL rerun
    [line] = [r.getMessage() for r in caplog.records if "event=query_complete" in r.getMessage()]
    assert "outcome=success" in line and "history_status=failed history_error=busy" in line
    assert db.connects == 5  # the busy write never connected
