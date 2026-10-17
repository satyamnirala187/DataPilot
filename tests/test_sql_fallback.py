"""Tests for the Gemini -> Groq SQL-generation fallback (backend/app/query_service.py).

Gemini and Groq are fakes, or the real provider modules over fake clients; time is a simulated
clock and the database a fake executor. The validator is the real one. No live provider or
database calls; the real GROQ_API_KEY is blanked for every test by tests/conftest.py.
"""

import json
import logging
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from google.genai import errors as genai_errors
from pydantic import SecretStr

from app import groq_sql, main, nl_to_sql
from app.config import settings
from app.db_executor import QueryExecutionError, QueryResult
from app.nl_to_sql import SQLGenerationError
from app.query_service import (FALLBACK_KINDS, FALLBACK_NEEDS_SECONDS, FALLBACK_REQUEST_BUDGET_SECONDS,
                               GEMINI_RETRY_DELAYS, SQL_ATTEMPT_SECONDS, QueryServiceError, run_business_query)
from app.rate_limiter import RateLimiter
from app.request_log import QueryMetrics
from app.sql_validator import validate_sql

QUESTION = "How many orders do we have?"
GEMINI_SQL = "SELECT ROUND(SUM(oi.quantity * oi.unit_price), 2) AS revenue FROM order_items AS oi"
GROQ_SQL = "SELECT COUNT(DISTINCT o.order_id) AS orders FROM orders AS o"
RESULT = QueryResult(columns=["orders"], rows=[[48213]], truncated=False)  # a value no timing field contains
INSIGHT = "There are 48,213 orders."
UNAVAILABLE_MESSAGE = "The AI service is unavailable right now. Please try again later."
FAKE_GROQ_KEY = "gsk_FAKE-fallback-key-0123456789"
ELIGIBLE = ["rate_limited", "unavailable", "timeout", "model_unavailable", "not_configured", "invalid_response",
            "empty_response"]


def ok(sql, seconds=1.0):
    return seconds, sql


def err(kind, seconds=1.0, **extra):
    return seconds, SQLGenerationError(kind, f"simulated {kind}", **extra)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Provider:
    """A fake SQL provider. Given (seconds, SQL or error) outcomes, each call takes that simulated
    time and returns the SQL or raises the error, in order. Given a function, it calls it."""

    def __init__(self, clock, outcomes):
        self.clock, self.calls = clock, 0
        self.outcomes = outcomes if callable(outcomes) else list(outcomes)

    def __call__(self, question):
        self.calls += 1
        if callable(self.outcomes):
            return self.outcomes(question)
        seconds, outcome = self.outcomes.pop(0)
        self.clock.now += seconds
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class Pipeline:
    """run_business_query with fake Gemini, Groq (None: not configured), executor and insight, and
    the real validator behind a spy."""

    def __init__(self, gemini, groq=None, *, execute_error=None, execute_seconds=0.5):
        self.clock = Clock()
        self.gemini = Provider(self.clock, gemini)
        self.groq = Provider(self.clock, groq) if groq is not None else None
        self.execute_error, self.execute_seconds = execute_error, execute_seconds
        self.validated, self.executed, self.summarized, self.sleeps = [], [], [], []
        self.metrics = QueryMetrics(request_id="test")

    def validate(self, sql):
        self.validated.append(sql)
        return validate_sql(sql)

    def execute(self, sql):
        self.executed.append(sql)
        self.clock.now += self.execute_seconds
        if self.execute_error is not None:
            raise self.execute_error
        return RESULT

    def summarize(self, *args):
        self.summarized.append(args)
        self.clock.now += 1.0
        return INSIGHT

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.clock.now += seconds

    def steps(self):
        return dict(generate=self.gemini, validate=self.validate, execute=self.execute, summarize=self.summarize,
                    sleep=self.sleep, clock=self.clock, fallback=self.groq)

    def run(self, question=QUESTION):
        return run_business_query(question, metrics=self.metrics, **self.steps())

    def fail(self, question=QUESTION) -> QueryServiceError:
        with pytest.raises(QueryServiceError) as caught:
            self.run(question)
        return caught.value

    @property
    def groq_calls(self):
        return self.groq.calls if self.groq is not None else 0


# --- A. Primary path ------------------------------------------------------------------------------

def test_gemini_success_never_calls_groq_and_keeps_the_insight():
    p = Pipeline([ok(GEMINI_SQL)], [ok(GROQ_SQL)])
    response = p.run()
    assert p.gemini.calls == 1 and p.groq_calls == 0
    assert response.sql == validate_sql(GEMINI_SQL) and p.executed == [validate_sql(GEMINI_SQL)]
    assert response.insight == INSIGHT and len(p.summarized) == 1
    m = p.metrics
    assert (m.sql_provider, m.fallback, m.fallback_reason, m.insight_status) == ("gemini", "not_used", None, "success")


# --- B. Eligible Gemini failures -> Groq ----------------------------------------------------------

def test_the_fallback_kinds_are_exactly_the_approved_matrix():
    assert FALLBACK_KINDS == set(ELIGIBLE)


@pytest.mark.parametrize("kind", ELIGIBLE)
def test_eligible_gemini_failure_falls_back_to_groq(kind):
    p = Pipeline([err(kind)], [ok(GROQ_SQL)])
    response = p.run()
    assert p.gemini.calls == 1 and p.groq_calls == 1
    assert response.sql == validate_sql(GROQ_SQL) and response.rows == RESULT.rows
    m = p.metrics
    assert (m.sql_provider, m.fallback, m.fallback_reason, m.stage, m.cause) == ("groq", "success", kind, None, None)


def test_gemini_timeout_after_its_full_attempt_still_falls_back():
    p = Pipeline([err("timeout", seconds=SQL_ATTEMPT_SECONDS)], [ok(GROQ_SQL, seconds=10.0)], execute_seconds=15.0)
    start = p.clock.now
    assert p.run().sql == validate_sql(GROQ_SQL)
    assert p.groq_calls == 1 and p.clock.now - start <= FALLBACK_REQUEST_BUDGET_SECONDS


def gemini_client(error):
    def generate_content(**kwargs):
        raise error
    return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))


@pytest.mark.parametrize("error", [
    httpx.ConnectError("connection refused"),
    OSError("network is unreachable"),
    genai_errors.ServerError(503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}}),
    genai_errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}),
])
def test_real_gemini_network_5xx_and_429_failures_fall_back(error):
    client = gemini_client(error)
    p = Pipeline(lambda q: nl_to_sql.generate_sql(q, client=client), [ok(GROQ_SQL)])
    assert p.run().sql == validate_sql(GROQ_SQL)
    assert p.gemini.calls == 1 and p.groq_calls == 1 and p.metrics.sql_provider == "groq"


# --- C. Ineligible Gemini failures -----------------------------------------------------------------

def test_gemini_request_failed_does_not_fall_back():
    p = Pipeline([err("request_failed")], [ok(GROQ_SQL)])
    error = p.fail()
    assert error.kind == "generation_failed" and p.groq_calls == 0 and p.executed == []
    m = p.metrics
    assert (m.fallback, m.fallback_reason, m.cause) == ("not_used", None, "gemini_request_failed")


@pytest.mark.parametrize("question", ["", "   "])
def test_an_invalid_question_reaches_neither_provider(question):
    p = Pipeline([ok(GEMINI_SQL)], [ok(GROQ_SQL)])
    assert p.fail(question).kind == "invalid_question"
    assert p.gemini.calls == 0 and p.groq_calls == 0


def test_an_invalid_question_kind_from_gemini_does_not_fall_back():
    p = Pipeline([err("invalid_question")], [ok(GROQ_SQL)])
    assert p.fail().kind == "invalid_question" and p.groq_calls == 0


# --- D. Budget boundary ----------------------------------------------------------------------------

LATEST_FALLBACK_START = FALLBACK_REQUEST_BUDGET_SECONDS - FALLBACK_NEEDS_SECONDS  # 24 s into the request


def test_fallback_starts_just_inside_the_budget_boundary():
    p = Pipeline([err("unavailable", seconds=LATEST_FALLBACK_START - 0.1)], [ok(GROQ_SQL)])
    assert p.run().sql == validate_sql(GROQ_SQL) and p.groq_calls == 1


@pytest.mark.parametrize("kind, service_kind", [
    ("unavailable", "generation_unavailable"),
    ("timeout", "generation_timeout"),
    ("invalid_response", "generation_failed"),
])
def test_without_time_for_groq_the_existing_gemini_error_is_returned(kind, service_kind):
    p = Pipeline([err(kind, seconds=LATEST_FALLBACK_START + 0.1)], [ok(GROQ_SQL)])
    error = p.fail()
    assert error.kind == service_kind and p.groq_calls == 0 and p.executed == []
    m = p.metrics
    assert (m.fallback, m.fallback_reason, m.cause) == ("skipped_budget", kind, f"gemini_{kind}")


def test_a_rate_limit_without_time_for_groq_keeps_its_retry_after():
    p = Pipeline([err("rate_limited", seconds=LATEST_FALLBACK_START + 0.1, limit_type="quota_exhausted",
                      retry_after=37)], [ok(GROQ_SQL)])
    error = p.fail()
    assert (error.kind, error.retry_after, p.metrics.limit_type) == ("rate_limited", 37, "quota_exhausted")


# --- E. Gemini retries ------------------------------------------------------------------------------

def test_with_groq_configured_gemini_gets_one_attempt():
    p = Pipeline([err("unavailable", seconds=0.3), ok(GEMINI_SQL)], [ok(GROQ_SQL)])
    assert p.run().sql == validate_sql(GROQ_SQL)
    assert p.gemini.calls == 1 and p.sleeps == [] and p.metrics.gemini_sql_attempts == 1


def test_without_groq_gemini_keeps_its_bounded_retries():
    p = Pipeline([err("unavailable", seconds=0.3)] * 3)
    assert p.fail().kind == "generation_unavailable"
    assert p.gemini.calls == 3 and p.sleeps == list(GEMINI_RETRY_DELAYS)
    m = p.metrics
    assert (m.fallback, m.sql_provider, m.cause) == (None, None, "gemini_unavailable")


def test_without_groq_a_retried_gemini_can_still_succeed():
    p = Pipeline([err("unavailable", seconds=0.3), ok(GEMINI_SQL)])
    assert p.run().sql == validate_sql(GEMINI_SQL) and p.gemini.calls == 2 and p.sleeps == [0.5]


# --- F. SQL safety -----------------------------------------------------------------------------------

def test_groq_sql_goes_through_the_same_validator_and_executor():
    p = Pipeline([err("rate_limited")], [ok(GROQ_SQL)])
    p.run()
    assert p.validated == [GROQ_SQL]  # exactly once, the real validator
    assert p.executed == [validate_sql(GROQ_SQL)]  # the validator's output, e.g. with its LIMIT


@pytest.mark.parametrize("unsafe", [
    "DELETE FROM orders",
    "SELECT 1; DROP TABLE orders",
    "SELECT usename FROM pg_catalog.pg_user",
    "UPDATE products SET price = 0",
])
def test_unsafe_groq_sql_is_blocked_and_never_retried(unsafe):
    p = Pipeline([err("unavailable")], [ok(unsafe)])
    error = p.fail()
    assert error.kind == "unsafe_sql" and p.executed == []
    assert p.gemini.calls == 1 and p.groq_calls == 1  # no further attempt from either provider
    assert p.metrics.cause == "validator_rejected"


def test_unsafe_gemini_sql_is_blocked_and_groq_is_never_called():
    p = Pipeline([ok("DROP TABLE orders")], [ok(GROQ_SQL)])
    assert p.fail().kind == "unsafe_sql"
    assert p.groq_calls == 0 and p.executed == [] and p.metrics.fallback == "not_used"


@pytest.mark.parametrize("db_kind", ["unavailable", "timeout", "permission_denied", "query_error"])
def test_a_database_failure_never_triggers_a_fallback(db_kind):
    p = Pipeline([ok(GEMINI_SQL)], [ok(GROQ_SQL)], execute_error=QueryExecutionError(db_kind, "simulated"))
    p.fail()
    assert p.groq_calls == 0 and len(p.executed) == 1


def test_a_database_failure_after_groq_sql_is_final():
    p = Pipeline([err("unavailable")], [ok(GROQ_SQL)], execute_error=QueryExecutionError("query_error", "simulated"))
    assert p.fail().kind == "query_failed"
    assert p.gemini.calls == 1 and p.groq_calls == 1 and len(p.executed) == 1


# --- G. Groq failures ----------------------------------------------------------------------------------

@pytest.mark.parametrize("groq_kind", ["unavailable", "timeout", "rate_limited", "invalid_response",
                                       "empty_response", "not_configured", "model_unavailable", "request_failed"])
def test_when_both_providers_fail_the_answer_is_one_neutral_503(groq_kind):
    p = Pipeline([err("rate_limited", limit_type="quota_exhausted", retry_after=3600)],
                 [err(groq_kind, retry_after=7 if groq_kind == "rate_limited" else None)])
    error = p.fail()
    assert (error.kind, str(error), error.retry_after) == ("generation_unavailable", UNAVAILABLE_MESSAGE, None)
    assert p.gemini.calls == 1 and p.groq_calls == 1 and p.executed == []  # no loop back to Gemini
    m = p.metrics
    assert (m.fallback, m.fallback_reason, m.stage, m.cause) == ("failed", "rate_limited", "sql", f"groq_{groq_kind}")


def groq_client(status, body):
    def handle(request):
        return httpx.Response(status, json=body)
    return httpx.Client(transport=httpx.MockTransport(handle), headers={"Authorization": f"Bearer {FAKE_GROQ_KEY}"})


def test_a_real_groq_429_becomes_the_neutral_503_without_provider_details():
    body = {"error": {"message": f"Rate limit reached for key {FAKE_GROQ_KEY}", "type": "tokens",
                      "code": "rate_limit_exceeded"}}
    p = Pipeline([err("unavailable")], lambda q: groq_sql.generate_sql(q, client=groq_client(429, body)))
    error = p.fail()
    assert (error.kind, str(error)) == ("generation_unavailable", UNAVAILABLE_MESSAGE)
    assert p.metrics.cause == "groq_rate_limited" and FAKE_GROQ_KEY not in repr(error)


def test_a_real_groq_answer_flows_through_the_pipeline():
    body = {"choices": [{"message": {"content": json.dumps({"sql": GROQ_SQL})}, "finish_reason": "stop"}]}
    p = Pipeline([err("timeout")], lambda q: groq_sql.generate_sql(q, client=groq_client(200, body)))
    assert p.run().sql == validate_sql(GROQ_SQL) and p.validated == [GROQ_SQL]


# --- H. Insight ------------------------------------------------------------------------------------------

def test_a_fallback_answer_skips_the_gemini_insight_but_keeps_the_result():
    p = Pipeline([err("rate_limited")], [ok(GROQ_SQL)])
    response = p.run()
    assert response.insight is None and p.summarized == []
    assert response.rows == RESULT.rows and response.visualization.type == "kpi"
    assert p.metrics.insight_status == "skipped_fallback"


# --- J. Configuration ------------------------------------------------------------------------------------

def test_without_a_groq_key_the_pipeline_is_gemini_only(monkeypatch):
    groq = Provider(Clock(), [ok(GROQ_SQL)])
    monkeypatch.setattr(groq_sql, "generate_sql", groq)
    assert settings.groq_api_key is None  # blanked by tests/conftest.py
    p = Pipeline([err("rate_limited")])
    assert p.fail().kind == "rate_limited" and groq.calls == 0


def test_with_a_groq_key_the_groq_provider_is_the_default_fallback(monkeypatch):
    groq = Provider(Clock(), [ok(GROQ_SQL)])
    monkeypatch.setattr(groq_sql, "generate_sql", groq)  # the provider itself stays a fake
    monkeypatch.setattr(settings, "groq_api_key", SecretStr(FAKE_GROQ_KEY))
    p = Pipeline([err("rate_limited")])
    assert p.run().sql == validate_sql(GROQ_SQL) and groq.calls == 1


def test_a_configured_groq_key_selects_the_50_second_budget(monkeypatch):
    # Gemini answers in 20 s, the database takes 13 s: 17 s of 50 s remain, so the insight runs.
    # Without the key (45 s) only 12 s would remain and it would be skipped for the budget.
    monkeypatch.setattr(groq_sql, "generate_sql", Provider(Clock(), [ok(GROQ_SQL)]))
    monkeypatch.setattr(settings, "groq_api_key", SecretStr(FAKE_GROQ_KEY))
    with_key = Pipeline([ok(GEMINI_SQL, seconds=20.0)], execute_seconds=13.0)
    assert with_key.run().insight == INSIGHT and with_key.metrics.insight_status == "success"
    monkeypatch.setattr(settings, "groq_api_key", None)
    without_key = Pipeline([ok(GEMINI_SQL, seconds=20.0)], execute_seconds=13.0)
    assert without_key.run().insight is None and without_key.metrics.insight_status == "skipped_budget"


# --- I and K. Through the API: History and logs ----------------------------------------------------------

@pytest.fixture
def api(monkeypatch, caplog):
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(max_requests=100, window_seconds=60))
    caplog.set_level(logging.DEBUG)

    def ask(p):
        monkeypatch.setattr(main, "run_business_query", lambda q, **kw: run_business_query(q, **p.steps(), **kw))
        return TestClient(main.app, raise_server_exceptions=False).post("/query", json={"question": QUESTION})
    return ask


def summary(caplog):
    [record] = [r for r in caplog.records if r.name == "app.request_log"]
    return dict(part.split("=", 1) for part in record.getMessage().split())


def test_a_fallback_answer_is_one_normal_history_entry(api, fake_history, caplog):
    p = Pipeline([err("rate_limited")], [ok(GROQ_SQL)])
    response = api(p)
    assert response.status_code == 200 and response.json()["analysis_id"] == fake_history.ANALYSIS_ID
    assert len(fake_history.calls) == 1 and fake_history.calls[0][0].sql == validate_sql(GROQ_SQL)
    assert p.gemini.calls == 1 and p.groq_calls == 1 and len(p.executed) == 1
    fields = summary(caplog)
    assert (fields["sql_provider"], fields["fallback"], fields["fallback_reason"]) == ("groq", "success", "rate_limited")
    assert fields["insight_status"] == "skipped_fallback" and fields["history_status"] == "saved"


def test_both_providers_failing_is_a_neutral_503_with_nothing_stored(api, fake_history, caplog):
    p = Pipeline([err("unavailable")], [err("timeout")])
    response = api(p)
    assert response.status_code == 503 and "retry-after" not in response.headers
    assert response.json() == {"error": {"code": "generation_unavailable", "message": UNAVAILABLE_MESSAGE}}
    assert "gemini" not in response.text.lower() and "groq" not in response.text.lower()
    assert fake_history.calls == [] and p.executed == []
    fields = summary(caplog)
    assert (fields["stage"], fields["cause"], fields["fallback"]) == ("sql", "groq_timeout", "failed")
    assert fields["fallback_reason"] == "unavailable" and "sql_provider" not in fields


def test_a_gemini_only_log_line_has_no_fallback_fields(api, caplog):
    assert api(Pipeline([ok(GEMINI_SQL)])).status_code == 200
    fields = summary(caplog)
    assert fields["sql_provider"] == "gemini" and "fallback" not in fields and "fallback_reason" not in fields


def test_logs_never_contain_keys_sql_rows_or_provider_bodies(api, monkeypatch, caplog):
    monkeypatch.setattr(settings, "groq_api_key", SecretStr(FAKE_GROQ_KEY))
    body = {"error": {"message": f"Rate limit reached for key {FAKE_GROQ_KEY}", "code": "rate_limit_exceeded"}}
    for groq in (lambda q: groq_sql.generate_sql(q, client=groq_client(429, body)), [ok(GROQ_SQL)]):
        api(Pipeline([err("unavailable")], groq))
    lines = [m for m in caplog.messages if "event=query_complete" in m]
    assert len(lines) == 2 and "cause=groq_rate_limited" in lines[0] and "sql_provider=groq" in lines[1]
    for secret in (FAKE_GROQ_KEY, "Rate limit reached", GROQ_SQL, "COUNT(DISTINCT", "48213"):
        assert secret not in caplog.text
