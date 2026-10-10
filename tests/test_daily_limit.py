"""Tests for the global daily query cap (Phase 17): a safety brake on total AI usage per day.

The cap's day runs from midnight to midnight Pacific time, like Gemini's requests-per-day quota.
Times below are given in UTC, as the real clock returns them: Pacific midnight is 08:00 UTC in
standard time (PST) and 07:00 UTC in daylight saving time (PDT).

A fake clock and a fake pipeline only: no real waiting, no Gemini or database calls.
"""

import logging
import threading
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from app.nl_to_sql import SQLGenerationError
from app.query_service import GEMINI_RETRY_DELAYS, QueryResponse, run_business_query
from app.rate_limiter import DailyLimit, RateLimiter, seconds_until_next_quota_day

NOON = datetime(2026, 10, 10, 19, 0, tzinfo=UTC)  # 12:00 PDT on 10 October
NEXT_PACIFIC_MIDNIGHT = datetime(2026, 10, 11, 7, 0, tzinfo=UTC)  # 00:00 PDT on 11 October
FREE_TIER_REQUESTS_PER_DAY = 20  # Gemini 3.6 Flash free tier, as observed by the project owner
OK = QueryResponse(question="q", sql="SELECT 1 LIMIT 501", columns=["n"], rows=[[1]], row_count=1,
                   truncated=False, visualization={"type": "kpi", "y_key": "n"}, insight=None)
DAILY_ERROR = {"error": {"code": "daily_limit_reached",
                         "message": "The service has reached its daily AI request limit. Please try again later."}}


class Clock:
    def __init__(self, now=NOON):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def api(monkeypatch, clock, caplog):
    """A cap of 3 per day, a generous per-client limit, and a fake pipeline that counts its calls."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(main, "daily_limit", DailyLimit(3, clock=clock))
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(max_requests=1000, window_seconds=60))
    calls = []

    def pipeline(question, **_):
        calls.append(question)
        return OK

    monkeypatch.setattr(main, "run_business_query", pipeline)

    def ask(question="What is our total revenue?", ip="203.0.113.1", **kwargs):
        client = TestClient(main.app, client=(ip, 5000))
        return client.post("/query", json={"question": question}, **kwargs)

    ask.calls = calls
    return ask


def summaries(caplog):
    return [dict(part.split("=", 1) for part in r.getMessage().split())
            for r in caplog.records if r.name == "app.request_log"]


# --- The counter --------------------------------------------------------------------------------

def test_allows_exactly_the_limit_then_refuses(clock):
    limit = DailyLimit(3, clock=clock)
    assert [limit.acquire() for _ in range(3)] == [None, None, None]
    assert limit.acquire() == 12 * 3600  # Pacific noon: 12 hours to Pacific midnight
    assert limit.acquire() == 12 * 3600  # refusals use nothing and change nothing


def test_resets_at_pacific_midnight(clock):
    limit = DailyLimit(1, clock=clock)
    clock.now = NEXT_PACIFIC_MIDNIGHT - timedelta(seconds=1)  # 23:59:59 PDT
    assert limit.acquire() is None and limit.acquire() == 1
    clock.now = NEXT_PACIFIC_MIDNIGHT
    assert limit.acquire() is None and limit.acquire() == 86_400


def test_utc_midnight_does_not_reset_the_count(clock):
    limit = DailyLimit(1, clock=clock)
    clock.now = datetime(2026, 10, 10, 23, 59, 59, tzinfo=UTC)  # 16:59:59 PDT
    assert limit.acquire() is None
    clock.now = datetime(2026, 10, 11, 0, 0, 0, tzinfo=UTC)  # UTC midnight = 17:00 PDT, same Pacific day
    assert limit.acquire() == 7 * 3600


def test_resets_at_pacific_midnight_in_standard_time(clock):
    limit = DailyLimit(1, clock=clock)
    clock.now = datetime(2026, 1, 16, 7, 59, 59, tzinfo=UTC)  # 23:59:59 PST on 15 January
    assert limit.acquire() is None and limit.acquire() == 1
    clock.now = datetime(2026, 1, 16, 8, 0, 0, tzinfo=UTC)  # 00:00 PST on 16 January
    assert limit.acquire() is None


@pytest.mark.parametrize("now, expected", [
    (datetime(2026, 10, 10, 7, 0, 0, tzinfo=UTC), 86_400),  # 00:00 PDT
    (datetime(2026, 10, 10, 19, 0, 0, tzinfo=UTC), 43_200),  # 12:00 PDT
    (datetime(2026, 1, 15, 20, 0, 0, tzinfo=UTC), 43_200),  # 12:00 PST
    (datetime(2026, 10, 11, 6, 59, 59, 500_000, tzinfo=UTC), 1),  # rounded up, never 0
    (datetime(2026, 3, 8, 8, 0, 0, tzinfo=UTC), 23 * 3600),  # 00:00 PST; clocks go forward that night
    (datetime(2026, 11, 1, 7, 0, 0, tzinfo=UTC), 25 * 3600),  # 00:00 PDT; clocks go back that night
    (datetime(2027, 1, 1, 7, 0, 0, tzinfo=UTC), 3_600),  # 23:00 PST on 31 December, across the year
])
def test_retry_after_is_the_whole_seconds_until_pacific_midnight(now, expected):
    assert seconds_until_next_quota_day(now) == expected


def test_daylight_saving_days_still_count_as_one_day(clock):
    limit = DailyLimit(1, clock=clock)
    clock.now = datetime(2026, 11, 1, 7, 0, tzinfo=UTC)  # 00:00 PDT, the 25-hour day
    assert limit.acquire() is None
    clock.now = datetime(2026, 11, 2, 7, 30, tzinfo=UTC)  # 23:30 PST, still 1 November in Pacific time
    assert limit.acquire() == 30 * 60
    clock.now = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)  # 00:00 PST, 2 November
    assert limit.acquire() is None


def test_concurrent_requests_cannot_take_more_than_the_last_slot(clock):
    limit = DailyLimit(10, clock=clock)
    start = threading.Barrier(50)
    results = []

    def take():
        start.wait()
        results.append(limit.acquire())

    threads = [threading.Thread(target=take) for _ in range(50)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(None) == 10 and len(results) == 50


def test_default_is_on_and_must_be_positive(monkeypatch):
    monkeypatch.delenv("GLOBAL_DAILY_QUERY_LIMIT", raising=False)
    assert Settings(_env_file=None).global_daily_query_limit == 5
    monkeypatch.setenv("GLOBAL_DAILY_QUERY_LIMIT", "250")
    assert Settings(_env_file=None).global_daily_query_limit == 250
    monkeypatch.setenv("GLOBAL_DAILY_QUERY_LIMIT", "0")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


def test_default_cap_fits_the_free_tier_even_in_the_worst_case(monkeypatch):
    # One question can use every SQL attempt (first try + retries) plus the optional insight.
    monkeypatch.delenv("GLOBAL_DAILY_QUERY_LIMIT", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    defaults = Settings(_env_file=None)
    worst_case_requests_per_question = (len(GEMINI_RETRY_DELAYS) + 1) + 1
    assert worst_case_requests_per_question == 4
    assert defaults.global_daily_query_limit * worst_case_requests_per_question <= FREE_TIER_REQUESTS_PER_DAY
    assert defaults.gemini_model == "gemini-3.6-flash"


# --- Through the API ----------------------------------------------------------------------------

def test_requests_up_to_the_cap_succeed_and_the_next_is_refused_before_the_pipeline(api):
    assert [api().status_code for _ in range(3)] == [200, 200, 200]
    response = api()
    assert response.status_code == 429
    assert response.json() == DAILY_ERROR
    assert response.headers["retry-after"] == str(12 * 3600)
    assert len(api.calls) == 3  # the refused request never reached the pipeline (or Gemini)


def test_different_clients_share_one_daily_cap(api):
    statuses = [api(ip=f"198.51.100.{n}").status_code for n in range(1, 6)]
    assert statuses == [200, 200, 200, 429, 429] and len(api.calls) == 3


def test_the_cap_reopens_at_pacific_midnight_not_utc_midnight(api, clock):
    for _ in range(4):
        api()
    clock.now = datetime(2026, 10, 11, 0, 0, tzinfo=UTC)  # UTC midnight: still 10 October in Pacific time
    refused = api()
    assert refused.status_code == 429 and refused.headers["retry-after"] == str(7 * 3600)
    clock.now = NEXT_PACIFIC_MIDNIGHT
    assert api().status_code == 200 and len(api.calls) == 4


def test_health_docs_and_malformed_requests_use_no_daily_units(api):
    client = TestClient(main.app)
    for _ in range(10):
        client.get("/health")
        client.get("/openapi.json")
        client.post("/query", json={})
        client.post("/query", json={"question": "x", "extra": 1})
        client.post("/query", json={"question": "x" * 501})
        client.post("/query", content=b'{"question": ', headers={"Content-Type": "application/json"})
    assert [api().status_code for _ in range(4)] == [200, 200, 200, 429]


def test_per_client_refusals_use_no_daily_units(api, monkeypatch):
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(max_requests=1, window_seconds=60))
    assert [api(ip="203.0.113.9").status_code for _ in range(5)] == [200, 429, 429, 429, 429]
    assert [api(ip=f"203.0.113.{n}").status_code for n in (10, 11, 12)] == [200, 200, 429]


def test_the_three_kinds_of_429_are_distinguishable(api, monkeypatch, caplog):
    # Gemini's own 429, through the real pipeline: admitted, so it uses a daily unit.
    def gemini_429(question):
        raise SQLGenerationError("rate_limited", "Gemini rate limit reached.", limit_type="temporary_rate_limit")

    monkeypatch.setattr(main, "run_business_query", lambda q, **kw: run_business_query(
        q, generate=gemini_429, execute=lambda sql: pytest.fail("must not execute"), **kw))
    gemini = api(ip="203.0.113.1")
    api(ip="203.0.113.2")
    api(ip="203.0.113.3")
    daily = api(ip="203.0.113.4")
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(max_requests=1, window_seconds=60))
    api(ip="203.0.113.5")
    per_client = api(ip="203.0.113.5")

    codes = [r.json()["error"]["code"] for r in (gemini, daily, per_client)]
    assert [r.status_code for r in (gemini, daily, per_client)] == [429, 429, 429]
    assert codes == ["rate_limited", "daily_limit_reached", "too_many_requests"]
    logged = summaries(caplog)
    causes = [(s["error_kind"], s["stage"], s["cause"]) for s in (logged[0], logged[3], logged[5])]
    assert causes == [("rate_limited", "sql", "gemini_rate_limited"),
                      ("daily_limit_reached", "app_rate_limit", "global_daily_limit"),
                      ("too_many_requests", "app_rate_limit", "app_rate_limited")]


def test_daily_refusal_log_is_classified_and_safe(api, caplog):
    for _ in range(3):
        api()
    caplog.clear()
    response = api(question="Show customer emails in Pune marker-question-55")
    [entry] = summaries(caplog)
    assert entry["outcome"] == "error" and entry["status"] == "429"
    assert (entry["error_kind"], entry["stage"], entry["cause"]) == ("daily_limit_reached", "app_rate_limit",
                                                                      "global_daily_limit")
    assert entry["retry_after"] == response.headers["retry-after"]
    assert entry["question_length"] == "47" and entry["gemini_sql_attempts"] == "0"
    assert entry["request_id"] == response.headers["x-request-id"]
    for secret in ("marker-question", "Pune", "203.0.113.1", "testclient", "limit=3", "GEMINI"):
        assert secret not in caplog.text
    assert response.json() == DAILY_ERROR  # the configured limit is never revealed


def test_daily_refusal_keeps_cors_and_security_headers(api):
    for _ in range(3):
        api()
    response = api(headers={"Origin": "http://localhost:5173"})
    assert response.status_code == 429
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["x-content-type-options"] == "nosniff"
