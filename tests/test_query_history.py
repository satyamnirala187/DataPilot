"""Tests that POST /query stores successful answers as History (Phase 19, batch 2).

The real pipeline (run_business_query) runs with fake Gemini and database steps. History goes to
conftest's FakeHistory, or to the real store with a fake connection; nothing reaches a database or
Gemini.
"""

import logging

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import auth, history_store, main
from app.config import settings
from app.db_executor import QueryExecutionError, QueryResult
from app.history_store import HistoryResult
from app.insight_service import InsightError
from app.nl_to_sql import SQLGenerationError
from app.query_service import run_business_query
from app.rate_limiter import DailyLimit, RateLimiter
from app.sql_validator import validate_sql

QUESTION = "Which category earns the most? marker-question-51d0"
SQL = "SELECT c.name AS category, 7 AS revenue FROM categories AS c"
RESULT = QueryResult(columns=["category", "revenue"], rows=[["marker-row-Books", 7], ["Toys", 3]], truncated=False)
EMPTY = QueryResult(columns=["category", "revenue"], rows=[], truncated=False)
APP_URL = "postgresql://datapilot_app:app-secret-pw@app-db.example.test:5432/postgres"
FIELDS = ("question", "sql", "columns", "rows", "row_count", "truncated", "visualization", "insight")


class Steps:
    """Fake pipeline steps that count their calls."""

    def __init__(self, generate=None, execute=None, summarize=None):
        self.generated = self.executed = self.summarized = 0
        self._generate = generate or (lambda question: SQL)
        self._execute = execute or (lambda sql: RESULT)
        self._summarize = summarize or (lambda *args: "Books lead.")

    def generate(self, question):
        self.generated += 1
        return self._generate(question)

    def execute(self, sql):
        self.executed += 1
        return self._execute(sql)

    def summarize(self, *args):
        self.summarized += 1
        return self._summarize(*args)


@pytest.fixture(autouse=True)
def generous_rate_limit(monkeypatch):
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(max_requests=1000, window_seconds=60))


@pytest.fixture
def steps(monkeypatch):
    """The real pipeline with fake steps. Replace steps.* functions to make a step fail."""
    fake = Steps()
    monkeypatch.setattr(main, "run_business_query", lambda q, **kw: run_business_query(
        q, generate=fake.generate, validate=validate_sql, execute=fake.execute, summarize=fake.summarize,
        sleep=lambda s: None, **kw))
    return fake


def raises(error):
    def step(*args):
        raise error
    return step


def ask(question=QUESTION, **kwargs):
    return TestClient(main.app, raise_server_exceptions=False).post("/query", json={"question": question}, **kwargs)


def summary(caplog):
    [record] = [r for r in caplog.records if r.name == "app.request_log"]
    return dict(part.split("=", 1) for part in record.getMessage().split()), record


# --- Successful answers are stored ---------------------------------------------------------

def test_a_successful_answer_is_stored_once_and_returns_its_id(steps, fake_history):
    response = ask()
    assert response.status_code == 200
    body = response.json()
    assert body["analysis_id"] == fake_history.ANALYSIS_ID

    [(stored, account_id)] = fake_history.calls
    assert account_id == "demo"
    # The stored snapshot is exactly what the user received.
    assert stored.model_dump(include=set(FIELDS)) == {field: body[field] for field in FIELDS}
    assert stored.analysis_id is None  # the pipeline never sets it
    assert body["rows"] == [["marker-row-Books", 7], ["Toys", 3]] and body["insight"] == "Books lead."


def test_storing_reruns_nothing(steps, fake_history):
    ask()
    assert (steps.generated, steps.executed, steps.summarized) == (1, 1, 1)


def test_a_zero_row_answer_is_stored(steps, fake_history):
    steps._execute = lambda sql: EMPTY
    body = ask().json()
    [(stored, _)] = fake_history.calls
    assert (stored.row_count, stored.rows, stored.insight) == (0, [], None)
    assert body["analysis_id"] == fake_history.ANALYSIS_ID


@pytest.mark.parametrize("summarize", [raises(InsightError("unavailable", "simulated")),
                                       raises(RuntimeError("simulated"))])
def test_an_answer_without_an_insight_is_stored(steps, fake_history, summarize):
    steps._summarize = summarize
    response = ask()
    assert response.status_code == 200 and response.json()["insight"] is None
    [(stored, _)] = fake_history.calls
    assert stored.insight is None and stored.rows == RESULT.rows


def test_every_session_stores_under_the_same_demo_account(steps, fake_history, monkeypatch):
    # The real login gate: two separate logins (two session ids) share one account, so History
    # survives logout and new sessions.
    main.app.dependency_overrides.pop(auth.require_session, None)
    monkeypatch.setattr(settings, "demo_username", SecretStr("demo-user-fake"))
    monkeypatch.setattr(settings, "demo_password", SecretStr("fake-password-for-tests-only"))
    monkeypatch.setattr(settings, "demo_session_secret", SecretStr("fake-signing-secret-for-tests-0123456789"))
    monkeypatch.setattr(main, "login_limiter", RateLimiter(max_requests=100, window_seconds=60))
    client = TestClient(main.app)
    tokens = [client.post("/auth/login", json={"username": "demo-user-fake",
                                                "password": "fake-password-for-tests-only"}).json()["token"]
              for _ in range(2)]
    assert tokens[0] != tokens[1]
    for token in tokens:
        assert ask(headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert [account for _, account in fake_history.calls] == ["demo", "demo"]


def test_every_session_has_the_demo_account_and_the_token_format_is_unchanged():
    assert auth.DEMO_ACCOUNT_ID == "demo"
    sessions = [auth.Session("first-session-id-xxxxx", 1), auth.Session("other-session-id-xxxxx", 2)]
    assert [session.account_id for session in sessions] == ["demo", "demo"]
    assert "account_id" not in auth.Session.__dataclass_fields__  # derived, never stored or put in the token
    assert auth.TOKEN_PATTERN.pattern == r"v1\.([0-9]{1,12})\.([A-Za-z0-9_-]{22})\.([A-Za-z0-9_-]{43})"


# --- Failed requests store nothing ---------------------------------------------------------

def gemini_fails(kind):
    return raises(SQLGenerationError(kind, "simulated"))


def db_fails(kind):
    return raises(QueryExecutionError(kind, "simulated"))


@pytest.mark.parametrize("generate, execute, status", [
    (gemini_fails("rate_limited"), None, 429),
    (gemini_fails("timeout"), None, 503),
    (gemini_fails("unavailable"), None, 503),
    (gemini_fails("model_unavailable"), None, 503),
    (gemini_fails("request_failed"), None, 502),
    (gemini_fails("invalid_response"), None, 502),
    (lambda q: "DELETE FROM orders", None, 400),  # unsafe SQL
    (lambda q: "SELECT * FROM datapilot.analyses", None, 400),  # History tables are not queryable
    (None, db_fails("unavailable"), 503),
    (None, db_fails("timeout"), 504),
    (None, db_fails("permission_denied"), 400),
    (None, db_fails("query_error"), 422),
    (None, db_fails("result_too_large"), 422),
    (None, raises(RuntimeError("bug")), 500),
])
def test_a_failed_analysis_stores_nothing(steps, fake_history, generate, execute, status):
    steps._generate = generate or steps._generate
    steps._execute = execute or steps._execute
    response = ask()
    assert response.status_code == status
    assert "analysis_id" not in response.json()
    assert fake_history.calls == []


def test_an_unauthenticated_request_stores_nothing(steps, fake_history):
    main.app.dependency_overrides.pop(auth.require_session, None)
    assert ask().status_code == 401
    assert fake_history.calls == [] and steps.generated == 0


@pytest.mark.parametrize("payload", [{"question": ""}, {"question": "x" * 501}, {"text": "revenue?"}])
def test_an_invalid_request_stores_nothing(steps, fake_history, payload):
    assert TestClient(main.app).post("/query", json=payload).status_code == 400
    assert fake_history.calls == []


def test_a_whitespace_question_stores_nothing(steps, fake_history):
    assert ask("   ").status_code == 400
    assert fake_history.calls == []


def test_a_rate_limited_request_stores_nothing(steps, fake_history, monkeypatch):
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(max_requests=1, window_seconds=60))
    assert ask().status_code == 200
    assert ask().status_code == 429
    assert len(fake_history.calls) == 1  # only the answered question


def test_the_daily_cap_stores_nothing(steps, fake_history, monkeypatch):
    monkeypatch.setattr(main, "daily_limit", DailyLimit(limit=1))
    assert ask().status_code == 200
    response = ask()
    assert response.status_code == 429 and response.json()["error"]["code"] == "daily_limit_reached"
    assert len(fake_history.calls) == 1


# --- History failures never cost the user the answer ----------------------------------------

@pytest.mark.parametrize("result", [
    HistoryResult("disabled"),
    HistoryResult("failed", error="unavailable"),
    HistoryResult("failed", error="insert_failed"),
    HistoryResult("failed", error="RuntimeError"),
])
def test_a_history_failure_still_returns_the_full_answer(steps, fake_history, result):
    fake_history.result = result
    response = ask()
    assert response.status_code == 200
    body = response.json()
    assert body["analysis_id"] is None
    assert body["rows"] == RESULT.rows and body["insight"] == "Books lead." and body["sql"].startswith("SELECT")
    assert (steps.generated, steps.executed, steps.summarized) == (1, 1, 1)  # nothing retried or rerun


@pytest.fixture
def real_store(monkeypatch):
    """The real History store, with APP_DATABASE_URL set and psycopg.connect replaced by the test."""
    monkeypatch.setattr(main, "record_analysis", history_store.record_analysis)
    monkeypatch.setattr(settings, "app_database_url", SecretStr(APP_URL))
    return lambda connect: monkeypatch.setattr(history_store.psycopg, "connect", connect)


@pytest.mark.parametrize("error", [
    psycopg.OperationalError(f"connection to server at app-db.example.test failed: {APP_URL}"),
    RuntimeError(f"something unexpected with {APP_URL}"),
])
def test_the_real_store_failing_still_returns_the_answer(steps, real_store, error, caplog):
    caplog.set_level(logging.INFO)

    def connect(url, **kwargs):
        raise error

    real_store(connect)
    response = ask()
    assert response.status_code == 200 and response.json()["analysis_id"] is None
    assert response.json()["rows"] == RESULT.rows
    assert (steps.generated, steps.executed) == (1, 1)
    for secret in ("app-secret-pw", "app-db.example.test", "postgresql://", "datapilot_app"):
        assert secret not in response.text and secret not in caplog.text


def test_the_real_store_without_app_database_url_is_disabled(steps, real_store, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(settings, "app_database_url", None)
    real_store(lambda *a, **k: pytest.fail("must not connect"))
    response = ask()
    assert response.status_code == 200 and response.json()["analysis_id"] is None
    assert summary(caplog)[0]["history_status"] == "disabled"


# --- The summary log line -------------------------------------------------------------------

def test_a_stored_answer_is_logged_as_saved(steps, fake_history, caplog):
    caplog.set_level(logging.INFO)
    ask()
    fields, record = summary(caplog)
    assert (fields["outcome"], fields["status"], fields["history_status"]) == ("success", "200", "saved")
    assert fields["history_ms"].isdigit() and "history_error" not in fields
    assert record.levelno == logging.INFO
    assert fake_history.ANALYSIS_ID not in caplog.text  # the id is returned, not logged


@pytest.mark.parametrize("result, level", [
    (HistoryResult("failed", error="unavailable"), logging.WARNING),
    (HistoryResult("failed", error="RuntimeError"), logging.WARNING),
    (HistoryResult("disabled"), logging.INFO),
])
def test_a_history_problem_is_logged_but_the_request_is_a_success(steps, fake_history, caplog, result, level):
    caplog.set_level(logging.INFO)
    fake_history.result = result
    ask()
    fields, record = summary(caplog)
    assert (fields["outcome"], fields["status"], fields["history_status"]) == ("success", "200", result.status)
    assert fields.get("history_error") == result.error
    assert record.levelno == level


def test_a_failed_request_logs_no_history_fields(steps, fake_history, caplog):
    caplog.set_level(logging.INFO)
    steps._generate = gemini_fails("timeout")
    ask()
    fields, _ = summary(caplog)
    assert not any(name.startswith("history") for name in fields)


def test_history_logging_never_contains_the_answer(steps, real_store, caplog):
    caplog.set_level(logging.DEBUG)

    def connect(url, **kwargs):
        raise psycopg.OperationalError(f"failed for {APP_URL}")

    real_store(connect)
    ask()
    for text in ("marker-question-51d0", "marker-row-Books", "categories AS c", "Books lead.", "app-secret-pw",
                 "app-db.example.test"):
        assert text not in caplog.text
