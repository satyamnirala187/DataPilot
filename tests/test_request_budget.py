"""Tests for the SQL-generation timeout and the per-request time budget (Phase 16.2).

Time is simulated with a fake monotonic clock: each fake step advances it by a chosen duration,
so the budget rules are tested deterministically. No live Gemini or database calls.
"""

import inspect
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from google.genai import errors as genai_errors
from pydantic import SecretStr

from app import insight_service, main, nl_to_sql, query_service
from app.config import settings
from app import db_executor
from app.db_executor import CONNECT_TIMEOUT_SECONDS, QueryResult
from app.insight_service import InsightError
from app.nl_to_sql import SQL_GENERATION_TIMEOUT_MS, SQLGenerationError, generate_sql
from app.query_service import (
    INSIGHT_SECONDS,
    DB_STAGE_RESERVE_SECONDS,
    REQUEST_BUDGET_SECONDS,
    SAFETY_MARGIN_SECONDS,
    SQL_ATTEMPT_SECONDS,
    QueryServiceError,
    run_business_query,
)
from app.rate_limiter import RateLimiter

SQL = "SELECT ROUND(SUM(oi.quantity * oi.unit_price), 2) AS total_revenue FROM order_items AS oi"
RESULT = QueryResult(columns=["total_revenue"], rows=[[21304631.99]], truncated=False)
FRONTEND_TIMEOUT_SECONDS = 60  # frontend/src/services/api.js REQUEST_TIMEOUT_MS
RETRY_NEEDS = 0.5 + SQL_ATTEMPT_SECONDS + DB_STAGE_RESERVE_SECONDS  # first retry: wait + attempt + database reserve


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    sleeps: list


class Steps:
    """Fake generate / execute / summarize steps that take simulated time."""

    def __init__(self, clock, generation, execute_seconds=0.5, insight="Revenue is ₹21.30M."):
        self.clock, self.generation = clock, list(generation)  # [(seconds, kind or None)]
        self.execute_seconds, self.insight = execute_seconds, insight
        self.generate_calls = self.summarize_calls = 0
        clock.sleeps = []

    def generate(self, question):
        self.generate_calls += 1
        seconds, failure = self.generation.pop(0)
        self.clock.now += seconds
        if failure:
            raise SQLGenerationError(failure, "simulated failure")
        return SQL

    def execute(self, sql):
        self.clock.now += self.execute_seconds
        return RESULT

    def summarize(self, *args):
        self.summarize_calls += 1
        self.clock.now += 1.0
        if isinstance(self.insight, Exception):
            raise self.insight
        return self.insight

    def run(self):
        return run_business_query("What is our total revenue?", generate=self.generate, execute=self.execute,
                                  summarize=self.summarize, sleep=self.clock.sleep, clock=self.clock)


@pytest.fixture
def clock():
    return FakeClock()


# --- The SQL-generation client ---------------------------------------------------------------

def built_clients(monkeypatch):
    built = []
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("fake-key-for-test"))
    monkeypatch.setattr(nl_to_sql.genai, "Client", lambda **kwargs: built.append(kwargs))
    monkeypatch.setattr(insight_service.genai, "Client", lambda **kwargs: built.append(kwargs))
    nl_to_sql._default_client()
    insight_service._default_client()
    return built


def test_sql_generation_client_has_a_finite_timeout(monkeypatch):
    sql_client, _ = built_clients(monkeypatch)
    assert sql_client["http_options"].timeout == SQL_GENERATION_TIMEOUT_MS == 20_000


def test_no_sdk_retry_layer_is_enabled(monkeypatch):
    # retry_options=None makes the SDK stop after one attempt; app.query_service is the only retry layer.
    for client in built_clients(monkeypatch):
        assert client["http_options"].retry_options is None


def fake_gemini(error):
    def generate_content(**kwargs):
        calls.append(1)
        raise error
    calls = []
    return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)), calls


@pytest.mark.parametrize("error", [httpx.ReadTimeout("read timed out"), httpx.ConnectTimeout("timed out"), TimeoutError()])
def test_sdk_timeouts_become_the_timeout_kind(error):
    client, _ = fake_gemini(error)
    with pytest.raises(SQLGenerationError) as caught:
        generate_sql("Revenue?", client=client)
    assert caught.value.kind == "timeout"


# --- Retry rules -------------------------------------------------------------------------------

def test_sql_generation_timeout_is_not_retried(clock):
    steps = Steps(clock, [(SQL_ATTEMPT_SECONDS, "timeout")])
    with pytest.raises(QueryServiceError) as caught:
        steps.run()
    assert caught.value.kind == "generation_timeout"
    assert steps.generate_calls == 1 and clock.sleeps == []


def test_real_sdk_timeout_through_the_pipeline_is_not_retried():
    client, calls = fake_gemini(httpx.ReadTimeout("read timed out"))
    with pytest.raises(QueryServiceError) as caught:
        run_business_query("Revenue?", generate=lambda q: generate_sql(q, client=client),
                           execute=lambda sql: RESULT, summarize=lambda *a: None, sleep=lambda s: None)
    assert caught.value.kind == "generation_timeout" and len(calls) == 1


def test_rate_limit_429_is_still_not_retried(clock):
    steps = Steps(clock, [(0.3, "rate_limited")])
    with pytest.raises(QueryServiceError) as caught:
        steps.run()
    assert caught.value.kind == "rate_limited"
    assert steps.generate_calls == 1 and clock.sleeps == []


def test_real_sdk_429_through_the_pipeline_is_not_retried():
    error = genai_errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})
    client, calls = fake_gemini(error)
    with pytest.raises(QueryServiceError) as caught:
        run_business_query("Revenue?", generate=lambda q: generate_sql(q, client=client),
                           execute=lambda sql: RESULT, summarize=lambda *a: None, sleep=lambda s: None)
    assert caught.value.kind == "rate_limited" and len(calls) == 1


def test_fast_transient_failures_keep_the_bounded_retry_policy(clock):
    steps = Steps(clock, [(0.3, "unavailable"), (0.3, "unavailable"), (0.3, "unavailable")])
    with pytest.raises(QueryServiceError) as caught:
        steps.run()
    assert caught.value.kind == "generation_unavailable"
    assert steps.generate_calls == 3 and clock.sleeps == [0.5, 1.0]


def test_fast_transient_failure_then_success(clock):
    steps = Steps(clock, [(0.3, "unavailable"), (2.0, None)])
    assert steps.run().sql.startswith("SELECT ROUND")
    assert steps.generate_calls == 2 and clock.sleeps == [0.5]


def test_retry_scheduling_uses_the_database_reserve():
    # A retry needs the wait, a full SQL attempt and the database reserve: 0.5 + 20 + 20 = 40.5 s.
    assert DB_STAGE_RESERVE_SECONDS == 20.0
    assert RETRY_NEEDS == 40.5


def test_retry_is_skipped_when_the_budget_cannot_fit_another_attempt(clock):
    steps = Steps(clock, [(21.0, "unavailable"), (1.0, None)])
    with pytest.raises(QueryServiceError) as caught:
        steps.run()
    assert caught.value.kind == "generation_unavailable"
    assert steps.generate_calls == 1 and clock.sleeps == []


def test_retry_happens_just_inside_the_boundary(clock):
    # Failing after 4.4 s leaves 40.6 s, just enough for the 40.5 s a retry needs.
    steps = Steps(clock, [(REQUEST_BUDGET_SECONDS - RETRY_NEEDS - 0.1, "unavailable"), (1.0, None)])
    steps.run()
    assert steps.generate_calls == 2 and clock.sleeps == [0.5]


def test_retry_is_rejected_just_outside_the_boundary(clock):
    # Failing after 4.6 s leaves 40.4 s, just short of the 40.5 s a retry needs.
    steps = Steps(clock, [(REQUEST_BUDGET_SECONDS - RETRY_NEEDS + 0.1, "unavailable"), (1.0, None)])
    with pytest.raises(QueryServiceError):
        steps.run()
    assert steps.generate_calls == 1 and clock.sleeps == []


# --- Insight budget --------------------------------------------------------------------------------

def test_insight_is_skipped_when_too_little_time_remains_and_the_result_is_kept(clock):
    # SQL takes 20 s and the database 11 s: 14 s remain, less than the insight's 15 s timeout.
    steps = Steps(clock, [(20.0, None)], execute_seconds=11.0)
    response = steps.run()
    assert steps.summarize_calls == 0 and response.insight is None
    assert response.rows == RESULT.rows and response.row_count == 1 and response.truncated is False
    assert response.sql.startswith("SELECT ROUND") and response.visualization.type == "kpi"


def test_insight_runs_when_exactly_enough_time_remains(clock):
    # The insight needs its 15 s timeout plus the 1 s safety margin.
    needed = INSIGHT_SECONDS + SAFETY_MARGIN_SECONDS
    steps = Steps(clock, [(20.0, None)], execute_seconds=REQUEST_BUDGET_SECONDS - 20.0 - needed)
    assert steps.run().insight == "Revenue is ₹21.30M."
    assert steps.summarize_calls == 1


def test_insight_is_skipped_inside_the_safety_margin(clock):
    # 15.5 s left would fit the 15 s insight timeout, but not with the 1 s margin.
    steps = Steps(clock, [(20.0, None)], execute_seconds=REQUEST_BUDGET_SECONDS - 20.0 - 15.5)
    assert steps.run().insight is None and steps.summarize_calls == 0


def test_insight_failure_inside_the_budget_still_returns_the_result(clock):
    steps = Steps(clock, [(1.0, None)], insight=InsightError("unavailable", "Gemini service error (HTTP 503)."))
    response = steps.run()
    assert response.insight is None and response.rows == RESULT.rows


def test_api_returns_200_when_the_insight_is_skipped_for_time(monkeypatch):
    clock = FakeClock()
    steps = Steps(clock, [(20.0, None)], execute_seconds=11.0)
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(max_requests=100, window_seconds=60))
    monkeypatch.setattr(main, "run_business_query", lambda question: steps.run())
    response = TestClient(main.app).post("/query", json={"question": "What is our total revenue?"})
    assert response.status_code == 200
    body = response.json()
    assert body["insight"] is None and body["rows"] == [[21304631.99]] and body["visualization"]["type"] == "kpi"


# --- The budget itself -------------------------------------------------------------------------------

def test_psycopg_gets_a_5_second_connect_timeout(monkeypatch):
    seen = {}

    def fake_connect(url, **kwargs):
        seen.update(kwargs)
        raise db_executor.psycopg.OperationalError("refused")

    monkeypatch.setattr(db_executor.psycopg, "connect", fake_connect)
    with pytest.raises(db_executor.QueryExecutionError):
        db_executor.execute_query("SELECT 1", database_url="postgresql://fake")
    assert seen["connect_timeout"] == CONNECT_TIMEOUT_SECONDS == 5


def test_the_reserve_covers_the_configured_database_controls():
    # Not a proof of a maximum: just that the reserve exceeds one connect attempt plus the statement timeout.
    assert DB_STAGE_RESERVE_SECONDS > CONNECT_TIMEOUT_SECONDS + settings.query_timeout_ms / 1000


def test_the_answer_fits_the_plan_and_the_budget_fits_the_frontend():
    # A full SQL attempt plus the database reserve fit the budget; the optional insight is what is
    # dropped when both run long (5 s would be left, less than the 16 s it needs).
    assert SQL_ATTEMPT_SECONDS + DB_STAGE_RESERVE_SECONDS <= REQUEST_BUDGET_SECONDS
    assert REQUEST_BUDGET_SECONDS - SQL_ATTEMPT_SECONDS - DB_STAGE_RESERVE_SECONDS < INSIGHT_SECONDS + SAFETY_MARGIN_SECONDS
    assert REQUEST_BUDGET_SECONDS < FRONTEND_TIMEOUT_SECONDS


def test_planned_timing_of_every_path_stays_within_the_budget():
    # Planned timings, assuming the database stage stays within its reserve.
    budget, sql, db, insight = REQUEST_BUDGET_SECONDS, SQL_ATTEMPT_SECONDS, DB_STAGE_RESERVE_SECONDS, INSIGHT_SECONDS
    latest_retry_start = budget - sql - db  # a retry only starts if attempt + reserve still fit
    latest_insight_end = budget - (insight + SAFETY_MARGIN_SECONDS) + insight
    paths = {
        "A normal, insight runs": latest_insight_end,
        "A normal, slow SQL and database (insight skipped)": sql + db,
        "B/C after retries (insight skipped)": latest_retry_start + sql + db,
        "D SQL timeout, first attempt": sql,
        "D SQL timeout after a retry": latest_retry_start + sql,
    }
    for name, seconds in paths.items():
        assert seconds <= budget < FRONTEND_TIMEOUT_SECONDS, name


def test_slowest_planned_path_stays_within_the_budget(clock):
    # The latest failure that still allows a retry, then a full-length attempt and a database stage
    # that uses its whole reserve: the insight is skipped and the request ends inside the budget.
    first = REQUEST_BUDGET_SECONDS - RETRY_NEEDS
    steps = Steps(clock, [(first, "unavailable"), (SQL_ATTEMPT_SECONDS, None)], execute_seconds=DB_STAGE_RESERVE_SECONDS)
    start = clock.now
    response = steps.run()
    assert clock.now - start <= REQUEST_BUDGET_SECONDS
    assert response.insight is None and steps.summarize_calls == 0


def test_request_timing_uses_a_monotonic_clock():
    default = inspect.signature(query_service.run_business_query).parameters["clock"].default
    assert default is time.monotonic


def test_generation_timeout_reaches_the_user_as_a_safe_503(monkeypatch):
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(max_requests=100, window_seconds=60))

    def timed_out(question):
        raise QueryServiceError("generation_timeout", "The AI service took too long to respond. Please try again.")

    monkeypatch.setattr(main, "run_business_query", timed_out)
    response = TestClient(main.app).post("/query", json={"question": "Revenue?"})
    assert response.status_code == 503
    assert response.json() == {"error": {"code": "generation_timeout",
                                         "message": "The AI service took too long to respond. Please try again."}}
