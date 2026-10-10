"""Tests for quota-aware Gemini 429 handling (Phase 16.3). No live Gemini calls.

Fixtures are real google.genai ClientError objects built the way the SDK builds them from an HTTP
response: APIError.details holds the whole JSON body, {"error": {"code", "message", "status",
"details": [...]}}, with google.rpc detail objects identified by "@type".
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.genai import errors as genai_errors

from app import main
from app.db_executor import QueryResult
from app.insight_service import generate_insight
from app.nl_to_sql import SQLGenerationError, describe_rate_limit, generate_sql
from app.query_service import RATE_LIMIT_MESSAGES, run_business_query
from app.rate_limiter import RateLimiter

RETRY_INFO = "type.googleapis.com/google.rpc.RetryInfo"
QUOTA_FAILURE = "type.googleapis.com/google.rpc.QuotaFailure"
PER_MINUTE = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
PER_DAY = "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
FAKE_KEY = "AIzaFAKE-test-key-0123456789"
SQL = "SELECT ROUND(SUM(oi.quantity * oi.unit_price), 2) AS total_revenue FROM order_items AS oi"
RESULT = QueryResult(columns=["total_revenue"], rows=[[21304631.99]], truncated=False)

GENERIC, TEMPORARY, QUOTA = (RATE_LIMIT_MESSAGES[k] for k in ("rate_limited", "temporary_rate_limit", "quota_exhausted"))


def retry_info(delay):
    return {"@type": RETRY_INFO, "retryDelay": delay}


def quota_failure(*quota_ids):
    return {"@type": QUOTA_FAILURE, "violations": [
        {"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
         "quotaId": quota_id, "quotaDimensions": {"location": "global", "model": "gemini-test"}, "quotaValue": "15"}
        for quota_id in quota_ids]}


def error_429(*details, message="You exceeded your current quota."):
    body = {"error": {"code": 429, "message": message, "status": "RESOURCE_EXHAUSTED", "details": list(details)}}
    return genai_errors.ClientError(429, body)


def gemini(error):
    calls = []

    def generate_content(**kwargs):
        calls.append(1)
        raise error

    return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)), calls


def ask_api(monkeypatch, error):
    """POST /query through the real pipeline, with Gemini raising `error` for SQL generation."""
    client, calls = gemini(error)
    slept = []
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(max_requests=100, window_seconds=60))
    monkeypatch.setattr(main, "run_business_query", lambda question, **_: run_business_query(
        question, generate=lambda q: generate_sql(q, client=client), execute=lambda sql: RESULT,
        summarize=lambda *a: None, sleep=slept.append))
    response = TestClient(main.app).post("/query", json={"question": "What is our total revenue?"})
    return response, calls, slept


# --- Through the API ---------------------------------------------------------------------------

def test_generic_429_without_details(monkeypatch):
    response, calls, slept = ask_api(monkeypatch, error_429())
    assert response.status_code == 429
    assert response.json() == {"error": {"code": "rate_limited", "message": GENERIC}}
    assert "retry-after" not in response.headers
    assert len(calls) == 1 and slept == []  # never retried


def test_retry_metadata_becomes_a_retry_after_header_without_a_retry(monkeypatch):
    response, calls, slept = ask_api(monkeypatch, error_429(retry_info("37s")))
    assert response.status_code == 429 and response.headers["retry-after"] == "37"
    assert response.json()["error"] == {"code": "rate_limited", "message": GENERIC}
    assert len(calls) == 1 and slept == []


def test_fractional_retry_delay_is_rounded_up(monkeypatch):
    response, _, _ = ask_api(monkeypatch, error_429(retry_info("12.2s")))
    assert response.headers["retry-after"] == "13"


@pytest.mark.parametrize("delay", ["soon", "-5s", "37", 37, None, "", "1e3s", "999999s", {"seconds": "x"}])
def test_malformed_retry_metadata_is_ignored(monkeypatch, delay):
    response, calls, _ = ask_api(monkeypatch, error_429(retry_info(delay)))
    assert response.status_code == 429
    assert "retry-after" not in response.headers
    assert response.json()["error"] == {"code": "rate_limited", "message": GENERIC}
    assert len(calls) == 1


def test_per_minute_quota_is_a_temporary_rate_limit(monkeypatch):
    response, calls, _ = ask_api(monkeypatch, error_429(quota_failure(PER_MINUTE), retry_info("20s")))
    assert response.json()["error"] == {"code": "rate_limited", "message": TEMPORARY}
    assert response.headers["retry-after"] == "20" and len(calls) == 1


def test_daily_quota_is_reported_as_exhausted_and_keeps_its_retry_after(monkeypatch):
    response, calls, slept = ask_api(monkeypatch, error_429(quota_failure(PER_DAY), retry_info("40s")))
    assert response.status_code == 429
    assert response.json()["error"] == {"code": "rate_limited", "message": QUOTA}
    assert response.headers["retry-after"] == "40"
    assert len(calls) == 1 and slept == []


@pytest.mark.parametrize("details, message", [
    ((quota_failure(PER_MINUTE),), TEMPORARY),
    ((quota_failure(PER_DAY),), QUOTA),
    ((), GENERIC),
])
def test_no_retry_after_without_valid_retry_info_for_any_type(monkeypatch, details, message):
    response, _, _ = ask_api(monkeypatch, error_429(*details))
    assert response.json()["error"]["message"] == message
    assert "retry-after" not in response.headers


@pytest.mark.parametrize("quota, message", [(PER_MINUTE, TEMPORARY), (PER_DAY, QUOTA), (None, GENERIC)])
def test_valid_retry_info_becomes_retry_after_for_every_type(monkeypatch, quota, message):
    details = ([quota_failure(quota)] if quota else []) + [retry_info("7.1s")]
    response, calls, slept = ask_api(monkeypatch, error_429(*details))
    assert response.status_code == 429
    assert response.json()["error"] == {"code": "rate_limited", "message": message}
    assert response.headers["retry-after"] == "8"
    assert len(calls) == 1 and slept == []


def test_mixed_per_minute_and_daily_quota_is_treated_as_exhausted(monkeypatch):
    response, _, _ = ask_api(monkeypatch, error_429(quota_failure(PER_MINUTE, PER_DAY)))
    assert response.json()["error"]["message"] == QUOTA


@pytest.mark.parametrize("details", [
    (quota_failure("SomeFutureQuotaName"),),
    (quota_failure(PER_MINUTE, "SomeFutureQuotaName"),),
    ({"@type": QUOTA_FAILURE, "violations": "not a list"},),
    ({"@type": "type.googleapis.com/google.rpc.Help", "links": []},),
])
def test_unknown_or_malformed_quota_metadata_falls_back_to_generic(monkeypatch, details):
    response, _, _ = ask_api(monkeypatch, error_429(*details))
    assert response.status_code == 429
    assert response.json()["error"] == {"code": "rate_limited", "message": GENERIC}


def test_provider_details_never_reach_the_response(monkeypatch):
    message = f"Quota exceeded for metric generativelanguage.googleapis.com on project 123456789012 key={FAKE_KEY}"
    response, _, _ = ask_api(monkeypatch, error_429(quota_failure(PER_DAY), retry_info("40s"), message=message))
    text = response.text
    for leak in (PER_DAY, "123456789012", FAKE_KEY, "generativelanguage", "RESOURCE_EXHAUSTED", "quotaId", "googleapis"):
        assert leak not in text


# --- Classification details ---------------------------------------------------------------------

def test_inner_error_body_shape_is_also_understood():
    # The SDK's replay path stores the inner error object instead of {"error": {...}}.
    inner = {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [quota_failure(PER_MINUTE), retry_info("5s")]}
    assert describe_rate_limit(inner) == ("temporary_rate_limit", 5)


@pytest.mark.parametrize("body", [None, "not a dict", [], {}, {"error": "text"}, {"error": {"details": "x"}}])
def test_unexpected_bodies_fall_back_safely(body):
    assert describe_rate_limit(body) == ("rate_limited", None)


def test_retry_delay_is_capped_and_duration_objects_are_understood():
    assert describe_rate_limit({"error": {"details": [retry_info("5000s")]}}) == ("rate_limited", 3600)
    assert describe_rate_limit({"error": {"details": [retry_info({"seconds": 3, "nanos": 500_000_000})]}}) == ("rate_limited", 4)
    assert describe_rate_limit({"error": {"details": [retry_info("0.2s")]}}) == ("rate_limited", 1)


def test_sql_generation_error_carries_the_classification():
    client, calls = gemini(error_429(quota_failure(PER_MINUTE), retry_info("9s")))
    with pytest.raises(SQLGenerationError) as caught:
        generate_sql("Revenue?", client=client)
    assert (caught.value.kind, caught.value.limit_type, caught.value.retry_after) == ("rate_limited", "temporary_rate_limit", 9)
    assert PER_MINUTE not in str(caught.value) and FAKE_KEY not in str(caught.value)


# --- Optional insight 429 ---------------------------------------------------------------------------

def test_insight_429_keeps_the_successful_result_and_is_not_retried(monkeypatch):
    insight_client, insight_calls = gemini(error_429(quota_failure(PER_MINUTE), retry_info("30s")))
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(max_requests=100, window_seconds=60))
    monkeypatch.setattr(main, "run_business_query", lambda question, **_: run_business_query(
        question, generate=lambda q: SQL, execute=lambda sql: RESULT,
        summarize=lambda *args: generate_insight(*args, client=insight_client)))
    response = TestClient(main.app).post("/query", json={"question": "What is our total revenue?"})
    assert response.status_code == 200 and "retry-after" not in response.headers
    body = response.json()
    assert body["insight"] is None
    assert body["rows"] == [[21304631.99]] and body["sql"].startswith("SELECT ROUND")
    assert body["visualization"]["type"] == "kpi"
    assert len(insight_calls) == 1
