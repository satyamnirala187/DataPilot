"""Tests for request observability (Phase 16.4): request IDs, stage timings and the one summary log
line per POST /query. Fake steps and a fake clock only; no live Gemini or database calls.
"""

import logging
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.genai import errors as genai_errors

from app import db_executor, main
from app.db_executor import QueryExecutionError, QueryResult, execute_query
from app.insight_service import InsightError
from app.nl_to_sql import SQLGenerationError, generate_sql
from app.query_service import run_business_query
from app.rate_limiter import RateLimiter
from app.request_log import QueryMetrics
from app.sql_validator import validate_sql

QUESTION = "Which customers in Pune spent the most? marker-question-7f3a"
SQL = "SELECT c.name AS marker_sql_9c1e, 1 AS total FROM categories AS c"
RESULT = QueryResult(columns=["marker_sql_9c1e", "total"], rows=[["marker-row-42", 1], ["Books", 2]], truncated=False)
KPI = QueryResult(columns=["total_revenue"], rows=[[21304631.99]], truncated=False)
EMPTY = QueryResult(columns=["total_revenue"], rows=[], truncated=False)
DATABASE_URL = "postgresql://app_user:hunter2@db.internal.example:5432/postgres"
FAKE_KEY = "AIzaFAKE-test-key-0123456789"
REQUEST_ID = re.compile(r"^[0-9a-f]{16}$")
BACKEND = Path(__file__).resolve().parents[1] / "backend"
TIMINGS = ("total_ms", "sql_ms", "validation_ms", "db_ms", "insight_ms")


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture(autouse=True)
def setup(monkeypatch, caplog):
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(max_requests=100, window_seconds=60))
    caplog.set_level(logging.INFO)


def ask(monkeypatch, *, generate=lambda q: SQL, execute=lambda sql: RESULT, summarize=lambda *a: "Books lead.",
        clock=None, question=QUESTION):
    clock = clock or FakeClock()
    steps = dict(generate=generate, validate=validate_sql, execute=execute, summarize=summarize,
                 sleep=clock.sleep, clock=clock)
    monkeypatch.setattr(main, "run_business_query", lambda q, **kw: run_business_query(q, **steps, **kw))
    return TestClient(main.app, raise_server_exceptions=False).post("/query", json={"question": question})


def summary(caplog):
    """The single summary line, parsed into a dict, and its log record."""
    [record] = [r for r in caplog.records if r.name == "app.request_log"]
    return dict(part.split("=", 1) for part in record.getMessage().split()), record


def fails(kind, **extra):
    def step(*args):
        raise SQLGenerationError(kind, "simulated", **extra) if kind else RuntimeError("unreachable")
    return step


def failing_db(kind):
    def execute(sql):
        raise QueryExecutionError(kind, f'invalid input syntax for type integer: "marker-value-55" at {DATABASE_URL}')
    return execute


def gemini_client(error):
    def generate_content(**kwargs):
        raise error
    return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))


def sequence(*outcomes):
    """A generate step that fails or succeeds in order: a kind string fails, None returns SQL."""
    remaining = list(outcomes)

    def generate(question):
        kind = remaining.pop(0)
        if kind:
            raise SQLGenerationError(kind, "simulated")
        return SQL
    return generate


# --- Logging setup ---------------------------------------------------------------------------------

def test_app_logs_at_info_and_libraries_stay_at_warning():
    # A fresh interpreter, as under uvicorn: pytest's own log capture changes logger levels.
    script = (
        "import logging, app.main\n"
        "assert logging.getLogger('app.request_log').isEnabledFor(logging.INFO)\n"
        # httpx logs every request URL at INFO; it must not inherit the app's level.
        "assert not logging.getLogger('httpx').isEnabledFor(logging.INFO)\n"
        "assert not logging.getLogger('google_genai').isEnabledFor(logging.INFO)\n"
        "logging.getLogger('app.request_log').info('event=probe')\n"
    )
    done = subprocess.run([sys.executable, "-c", script], cwd=BACKEND, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert "INFO [app.request_log] event=probe" in done.stderr


# --- The success line ------------------------------------------------------------------------------

def test_success_emits_one_summary_line_with_the_useful_fields(monkeypatch, caplog):
    response = ask(monkeypatch)
    assert response.status_code == 200
    fields, record = summary(caplog)
    assert record.levelno == logging.INFO
    assert fields["event"] == "query_complete" and fields["outcome"] == "success" and fields["status"] == "200"
    assert fields["gemini_sql_attempts"] == "1" and fields["rows"] == "2" and fields["truncated"] == "false"
    assert fields["visualization"] == "bar" and fields["insight_status"] == "success"
    assert fields["question_length"] == str(len(QUESTION))
    assert all(name in fields for name in TIMINGS)
    assert "error_kind" not in fields and "stage" not in fields


def test_summary_has_a_random_request_id_matching_the_response_header(monkeypatch, caplog):
    first = ask(monkeypatch).headers["x-request-id"]
    second = ask(monkeypatch).headers["x-request-id"]
    ids = [line.split("request_id=")[1].split()[0] for line in caplog.messages if "event=query_complete" in line]
    assert ids == [first, second] and first != second
    assert REQUEST_ID.match(first) and REQUEST_ID.match(second)


def test_a_client_supplied_request_id_is_ignored(monkeypatch, caplog):
    client = TestClient(main.app)
    monkeypatch.setattr(main, "run_business_query", lambda q, **kw: run_business_query(
        q, generate=lambda _: SQL, execute=lambda sql: RESULT, summarize=lambda *a: None, **kw))
    response = client.post("/query", json={"question": "q"}, headers={"X-Request-ID": "attacker\nevent=fake"})
    assert REQUEST_ID.match(response.headers["x-request-id"])
    assert "attacker" not in caplog.text


def test_stage_timings_come_from_the_monotonic_clock(monkeypatch, caplog):
    clock = FakeClock()

    def slow_generate(question):
        clock.now += 1.25
        return SQL

    def slow_execute(sql):
        clock.now += 0.5
        return RESULT

    def slow_summarize(*args):
        clock.now += 2.0
        return "Books lead."

    ask(monkeypatch, generate=slow_generate, execute=slow_execute, summarize=slow_summarize, clock=clock)
    fields, _ = summary(caplog)
    assert (fields["sql_ms"], fields["validation_ms"], fields["db_ms"], fields["insight_ms"]) == ("1250", "0", "500", "2000")


# --- Nothing sensitive is logged ---------------------------------------------------------------------

def leak_cases(monkeypatch):
    def db_unavailable(sql):
        return execute_query(sql, database_url=DATABASE_URL)

    def refuse(url, **kwargs):
        raise db_executor.psycopg.OperationalError(f'connection to server at "{url}" failed: password hunter2')

    monkeypatch.setattr(db_executor.psycopg, "connect", refuse)
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                      "message": f"Quota exceeded for project 123456789012 key={FAKE_KEY} at generativelanguage.googleapis.com",
                      "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                                   "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}
    gemini_429 = gemini_client(genai_errors.ClientError(429, body))
    gemini_500 = gemini_client(genai_errors.ServerError(500, {"error": {"message": f"upstream body key={FAKE_KEY}"}}))
    return {
        "success": dict(),
        "gemini_429": dict(generate=lambda q: generate_sql(q, client=gemini_429)),
        "gemini_500": dict(generate=lambda q: generate_sql(q, client=gemini_500)),
        "validator": dict(generate=lambda q: "SELECT marker_sql_9c1e FROM pg_catalog.pg_shadow_marker"),
        "db_unavailable": dict(execute=db_unavailable),
        "db_query_error": dict(execute=failing_db("query_error")),
        "insight_failure": dict(summarize=fails(None)),
    }


@pytest.mark.parametrize("case", ["success", "gemini_429", "gemini_500", "validator", "db_unavailable",
                                  "db_query_error", "insight_failure"])
def test_logs_never_contain_the_question_sql_rows_or_secrets(monkeypatch, caplog, case):
    ask(monkeypatch, **leak_cases(monkeypatch)[case])
    text = caplog.text
    for secret in ("marker-question", "Pune", "marker_sql", "SELECT", "marker-row", "marker-value", "pg_shadow", "pg_catalog",
                   "hunter2", "db.internal", "postgresql://", FAKE_KEY, "123456789012", "googleapis",
                   "RESOURCE_EXHAUSTED", "PerDay", "upstream body"):
        assert secret not in text, (case, secret)


def test_summary_values_are_always_single_tokens():
    line = QueryMetrics(request_id="abc", cause="two words\nevent=forged", visualization="x=y").summary()
    assert "\n" not in line and "event=forged" not in line
    assert line.split()[0] == "event=query_complete" and line.count("event=") == 1


# --- SQL-generation attempts -------------------------------------------------------------------------

@pytest.mark.parametrize("outcomes, attempts, status", [
    ((None,), 1, 200),
    (("unavailable", None), 2, 200),
    (("unavailable", "unavailable", None), 3, 200),
    (("unavailable", "unavailable", "unavailable"), 3, 503),
    (("rate_limited",), 1, 429),
    (("timeout",), 1, 503),
    (("unavailable", "timeout"), 2, 503),
])
def test_gemini_sql_attempts_are_counted_as_they_happen(monkeypatch, caplog, outcomes, attempts, status):
    response = ask(monkeypatch, generate=sequence(*outcomes))
    fields, _ = summary(caplog)
    assert response.status_code == status
    assert fields["gemini_sql_attempts"] == str(attempts)


def test_retry_waits_are_part_of_the_sql_stage(monkeypatch, caplog):
    ask(monkeypatch, generate=sequence("unavailable", None))
    fields, _ = summary(caplog)
    assert fields["sql_ms"] == "500"  # the 0.5 s wait between the two (instant) attempts


# --- Each failure is distinguishable -------------------------------------------------------------------

@pytest.mark.parametrize("steps, status, error_kind, stage, cause", [
    (dict(generate=fails("rate_limited", limit_type="quota_exhausted", retry_after=40)),
     429, "rate_limited", "sql", "gemini_rate_limited"),
    (dict(generate=sequence("unavailable", "unavailable", "unavailable")),
     503, "generation_unavailable", "sql", "gemini_unavailable"),
    (dict(generate=fails("timeout")), 503, "generation_timeout", "sql", "gemini_timeout"),
    (dict(generate=fails("invalid_response")), 502, "generation_failed", "sql", "gemini_invalid_response"),
    (dict(generate=fails("empty_response")), 502, "generation_failed", "sql", "gemini_empty_response"),
    (dict(generate=lambda q: "DELETE FROM orders"), 400, "unsafe_sql", "validation", "validator_rejected"),
    (dict(execute=failing_db("timeout")), 504, "query_timeout", "db", "db_timeout"),
    (dict(execute=failing_db("unavailable")), 503, "database_unavailable", "db", "db_unavailable"),
    (dict(execute=failing_db("query_error")), 422, "query_failed", "db", "db_query_error"),
    (dict(execute=failing_db("permission_denied")), 400, "query_not_allowed", "db", "db_permission_denied"),
])
def test_expected_failures_are_distinguishable_and_have_no_stack_trace(monkeypatch, caplog, steps, status,
                                                                       error_kind, stage, cause):
    response = ask(monkeypatch, **steps)
    assert response.status_code == status and response.json()["error"]["code"] == error_kind
    fields, record = summary(caplog)
    assert (fields["outcome"], fields["status"], fields["error_kind"], fields["stage"], fields["cause"]) == \
        ("error", str(status), error_kind, stage, cause)
    assert record.levelno == logging.WARNING
    # One line per request: no extra per-stage lines, no tracebacks.
    assert [r.name for r in caplog.records if r.name.startswith("app")] == ["app.request_log"]
    assert all(r.exc_info is None for r in caplog.records)
    assert "Traceback" not in caplog.text and 'File "' not in caplog.text
    assert "insight_status" not in fields


def test_failed_stage_timings_stop_at_the_failure(monkeypatch, caplog):
    ask(monkeypatch, generate=lambda q: "DELETE FROM orders")
    fields, _ = summary(caplog)
    assert "sql_ms" in fields and "validation_ms" in fields and "db_ms" not in fields


def test_gemini_429_is_distinguishable_from_the_app_rate_limit(monkeypatch, caplog):
    ask(monkeypatch, generate=fails("rate_limited", limit_type="temporary_rate_limit", retry_after=20))
    gemini, _ = summary(caplog)
    caplog.clear()

    monkeypatch.setattr(main, "rate_limiter", RateLimiter(max_requests=1, window_seconds=60))
    ask(monkeypatch)
    caplog.clear()
    response = ask(monkeypatch)
    app, record = summary(caplog)

    assert response.status_code == 429 and response.json()["error"]["code"] == "too_many_requests"
    assert (gemini["error_kind"], gemini["stage"], gemini["cause"]) == ("rate_limited", "sql", "gemini_rate_limited")
    assert gemini["limit_type"] == "temporary_rate_limit" and gemini["retry_after"] == "20"
    assert (app["error_kind"], app["stage"], app["cause"]) == ("too_many_requests", "app_rate_limit", "app_rate_limited")
    assert app["gemini_sql_attempts"] == "0" and "retry_after" in app
    assert "testclient" not in caplog.text and "127.0.0.1" not in caplog.text  # no client address


def test_malformed_request_is_logged_without_its_content(monkeypatch, caplog):
    response = TestClient(main.app).post("/query", json={"question": "marker-question", "extra": "x"})
    fields, _ = summary(caplog)
    assert response.status_code == 400 and REQUEST_ID.match(response.headers["x-request-id"])
    assert (fields["error_kind"], fields["stage"]) == ("invalid_request", "request")
    assert "marker-question" not in caplog.text


# --- The optional insight ------------------------------------------------------------------------------

@pytest.mark.parametrize("summarize, status, error", [
    (lambda *a: "Books lead.", "success", None),
    (fails(None), "failed", "RuntimeError"),
    (lambda *a: (_ for _ in ()).throw(InsightError("rate_limited", "Gemini rate limit reached.")), "failed", "rate_limited"),
    (lambda *a: (_ for _ in ()).throw(InsightError("unavailable", "Gemini service error (HTTP 503).")), "failed", "unavailable"),
])
def test_insight_outcome_is_logged_and_never_changes_the_answer(monkeypatch, caplog, summarize, status, error):
    response = ask(monkeypatch, summarize=summarize)
    fields, record = summary(caplog)
    assert response.status_code == 200 and response.json()["rows"] == RESULT.rows
    assert fields["outcome"] == "success" and fields["insight_status"] == status and record.levelno == logging.INFO
    assert fields.get("insight_error") == error and "insight_ms" in fields


def test_insight_skipped_for_the_budget(monkeypatch, caplog):
    clock = FakeClock()

    def slow_generate(question):
        clock.now += 20.0
        return SQL

    def slow_execute(sql):
        clock.now += 11.0  # 14 s left, the insight needs 16 s
        return RESULT

    summarized = []
    response = ask(monkeypatch, generate=slow_generate, execute=slow_execute, clock=clock,
                   summarize=lambda *a: summarized.append(1))
    fields, _ = summary(caplog)
    assert response.status_code == 200 and response.json()["insight"] is None and summarized == []
    assert fields["insight_status"] == "skipped_budget" and "insight_ms" not in fields


def test_insight_skipped_for_an_empty_result(monkeypatch, caplog):
    response = ask(monkeypatch, execute=lambda sql: EMPTY)
    fields, _ = summary(caplog)
    assert response.status_code == 200 and fields["insight_status"] == "skipped_empty" and fields["rows"] == "0"


# --- Timings ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("steps", [
    dict(), dict(generate=sequence("unavailable", None)), dict(generate=fails("timeout")),
    dict(generate=lambda q: "DELETE FROM orders"), dict(execute=failing_db("timeout")),
    dict(summarize=fails(None)), dict(execute=lambda sql: EMPTY),
])
def test_every_timing_is_a_non_negative_whole_number(monkeypatch, caplog, steps):
    ask(monkeypatch, **steps)
    fields, _ = summary(caplog)
    timings = {name: fields[name] for name in TIMINGS if name in fields}
    assert "total_ms" in timings
    assert all(value.isdigit() for value in timings.values()), timings


# --- Unexpected errors -------------------------------------------------------------------------------------

def test_unexpected_error_stays_sanitized_and_is_logged_with_its_request_id(monkeypatch, caplog):
    def broken(question):
        raise RuntimeError(f"connection failed: {DATABASE_URL}")

    response = ask(monkeypatch, generate=broken)
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error", "message": "Something went wrong. Please try again."}}
    request_id = response.headers["x-request-id"]
    fields, record = summary(caplog)
    assert record.levelno == logging.ERROR and fields["request_id"] == request_id
    assert (fields["error_kind"], fields["stage"], fields["cause"]) == ("internal_error", "sql", "RuntimeError")
    assert fields["gemini_sql_attempts"] == "1"
    assert f"request_id={request_id}" in caplog.text  # the traceback line carries the same ID
    assert "hunter2" not in caplog.text and "db.internal" not in caplog.text


# --- X-Request-ID ------------------------------------------------------------------------------------------

def test_request_id_header_on_success_handled_errors_and_cors_responses(monkeypatch):
    assert REQUEST_ID.match(ask(monkeypatch).headers["x-request-id"])
    assert REQUEST_ID.match(ask(monkeypatch, generate=fails("timeout")).headers["x-request-id"])
    response = TestClient(main.app).get("/health", headers={"Origin": "http://localhost:5173"})
    assert REQUEST_ID.match(response.headers["x-request-id"])
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_health_and_other_paths_are_not_summarised(caplog):
    TestClient(main.app).get("/health")
    TestClient(main.app).get("/nope")
    assert not [r for r in caplog.records if r.name == "app.request_log"]
