"""Tests for the result-size safeguard (Phase 17): an answer's JSON may not exceed max_result_bytes.

Only tiny limits and small values are used. The database-backed tests run normal queries only.
No Gemini calls.
"""

import json
import logging
from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import Settings, settings
from app.db_executor import QueryExecutionError, QueryResult, _json_rows, execute_query, json_size
from app.query_service import QueryServiceError, run_business_query
from app.rate_limiter import RateLimiter
from app.request_log import QueryMetrics

TOO_LARGE = {"error": {"code": "result_too_large",
                       "message": "The query result is too large to return safely. Please ask a more specific question."}}


def compact(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


# --- Measuring --------------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    None, True, False, 0, -7, 21304631.99, "", "plain", 'quote " and \\ backslash', "line\nbreak\ttab",
    "₹ é 中文 📊", ["a", 1, None, 2.5], [], {}, {"k": "v", "n": [1, {"x": None}]}, [["Books", 2], ["Toys", 3]],
])
def test_json_size_is_the_compact_utf8_json_length(value):
    assert json_size(value, 10**9) == compact(value)


def test_unicode_is_measured_in_encoded_bytes_not_characters():
    assert json_size("📊" * 10, 10**9) == 2 + 40  # 4 bytes per emoji, plus quotes
    assert json_size("é" * 10, 10**9) == 2 + 20


def test_measuring_stops_once_the_limit_is_passed():
    assert json_size("x" * 10_000, 100) > 100
    assert json_size(["x" * 50] * 1_000, 100) < 1_000  # stopped after the first few items


# --- Converting rows under the budget ----------------------------------------------------------

COLUMNS = ["category", "revenue"]
ROWS = [("Electronics", Decimal("7371769.52")), ("Home & Kitchen", Decimal("2788099.81"))]
CONVERTED = [["Electronics", 7371769.52], ["Home & Kitchen", 2788099.81]]
EXACT = compact(COLUMNS) + compact(CONVERTED)


def test_small_result_is_converted_in_full():
    assert _json_rows(COLUMNS, ROWS, 1_000_000) == CONVERTED


def test_a_result_exactly_at_the_limit_is_allowed_and_one_byte_over_is_refused():
    assert _json_rows(COLUMNS, ROWS, EXACT) == CONVERTED
    with pytest.raises(QueryExecutionError) as caught:
        _json_rows(COLUMNS, ROWS, EXACT - 1)
    assert caught.value.kind == "result_too_large"


def test_unicode_rows_are_limited_by_bytes():
    rows = [("📊" * 10,)]
    size = compact(["c"]) + compact([["📊" * 10]])  # 4 + 46 bytes, though only 10 characters
    assert _json_rows(["c"], rows, size) == [["📊" * 10]]
    with pytest.raises(QueryExecutionError):
        _json_rows(["c"], rows, size - 1)


def test_postgres_types_are_measured_after_conversion():
    columns = ["d", "ts", "amount", "missing"]
    rows = [(date(2026, 9, 30), datetime(2026, 9, 30, 12, 5), Decimal("10.50"), None)]
    expected = [["2026-09-30", "2026-09-30T12:05:00", 10.5, None]]
    size = compact(columns) + compact(expected)
    assert _json_rows(columns, rows, size) == expected
    with pytest.raises(QueryExecutionError):
        _json_rows(columns, rows, size - 1)


def test_an_oversized_result_is_refused_whole_never_cut_short():
    rows = [(f"row {n}",) for n in range(100)]
    with pytest.raises(QueryExecutionError) as caught:
        _json_rows(["c"], rows, 200)
    assert "row" not in str(caught.value)  # the message carries no result content


def test_setting_has_a_safe_default_and_must_be_positive(monkeypatch):
    monkeypatch.delenv("MAX_RESULT_BYTES", raising=False)
    assert Settings(_env_file=None).max_result_bytes == 1_000_000
    monkeypatch.setenv("MAX_RESULT_BYTES", "0")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


# --- Through the pipeline and the API ----------------------------------------------------------

def oversized(sql):
    raise QueryExecutionError("result_too_large", "The result is larger than the size limit.")


def test_pipeline_refuses_an_oversized_result_before_the_insight():
    summarized = []
    metrics = QueryMetrics(request_id="test")
    with pytest.raises(QueryServiceError) as caught:
        run_business_query("Everything?", generate=lambda q: "SELECT name FROM categories", execute=oversized,
                           summarize=lambda *a: summarized.append(a), metrics=metrics)
    assert caught.value.kind == "result_too_large" and summarized == []
    assert (metrics.stage, metrics.cause) == ("db", "db_result_too_large")


def test_api_returns_a_sanitized_422_and_logs_only_the_classification(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(max_requests=100, window_seconds=60))
    summarized = []

    def execute_with_tiny_limit(sql):
        rows = [("marker-row-content-31", Decimal("1.5"))] * 20
        return QueryResult(["marker_column", "v"], _json_rows(["marker_column", "v"], rows, 100), False)

    monkeypatch.setattr(main, "run_business_query", lambda q, **kw: run_business_query(
        q, generate=lambda _: "SELECT name AS marker_sql FROM categories", execute=execute_with_tiny_limit,
        summarize=lambda *a: summarized.append(a), **kw))
    response = TestClient(main.app).post("/query", json={"question": "Show everything marker-question"})

    assert response.status_code == 422 and response.json() == TOO_LARGE
    assert "x-request-id" in response.headers and summarized == []
    [line] = [r.getMessage() for r in caplog.records if r.name == "app.request_log"]
    fields = dict(part.split("=", 1) for part in line.split())
    assert (fields["status"], fields["error_kind"], fields["stage"], fields["cause"]) == \
        ("422", "result_too_large", "db", "db_result_too_large")
    assert "rows" not in fields and "insight_status" not in fields
    for leaked in ("marker-row", "marker_column", "marker_sql", "marker-question", "bytes"):
        assert leaked not in caplog.text and leaked not in response.text


# --- Against the real database: normal queries, tiny limits -----------------------------------

needs_database = pytest.mark.skipif(settings.readonly_database_url is None,
                                    reason="READONLY_DATABASE_URL is not configured")


@needs_database
def test_normal_query_is_unaffected_by_the_default_limit():
    result = execute_query("SELECT name FROM categories ORDER BY name")
    assert len(result.rows) == 10 and result.truncated is False


@needs_database
def test_a_tiny_limit_refuses_a_normal_query():
    with pytest.raises(QueryExecutionError) as caught:
        execute_query("SELECT name FROM categories ORDER BY name", max_bytes=50)
    assert caught.value.kind == "result_too_large"


@needs_database
def test_the_truncation_probe_row_is_not_counted_and_truncation_is_unchanged():
    # 4 rows exist; 3 are kept and measured, the 4th only shows there were more.
    sql = "SELECT category_id FROM categories ORDER BY category_id LIMIT 4"
    full = execute_query(sql, max_rows=3)
    exact = compact(["category_id"]) + compact(full.rows)
    result = execute_query(sql, max_rows=3, max_bytes=exact)
    assert result.rows == full.rows and len(result.rows) == 3 and result.truncated is True
