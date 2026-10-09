"""Tests for the FastAPI endpoints (backend/app/main.py). No live Gemini or database calls."""

import pytest
from fastapi.testclient import TestClient

from app import main
from app.chart_selector import Visualization
from app.query_service import QueryResponse, QueryServiceError
from app.rate_limiter import RateLimiter

client = TestClient(main.app)

SUCCESS = QueryResponse(
    question="What is our total revenue?",
    sql="SELECT SUM(oi.quantity * oi.unit_price) AS revenue FROM order_items AS oi LIMIT 500",
    columns=["revenue"],
    rows=[[21304631.99]],
    row_count=1,
    truncated=False,
    visualization=Visualization(type="kpi", y_key="revenue"),
    insight="Total revenue is ₹21.30M.",
)


@pytest.fixture(autouse=True)
def generous_rate_limit(monkeypatch):
    """These tests are about responses, not rate limiting (see test_security.py)."""
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(max_requests=10_000, window_seconds=60))


@pytest.fixture
def pipeline(monkeypatch):
    """Replace the real pipeline. Set .result to a QueryResponse or an exception."""
    state = type("State", (), {"result": SUCCESS, "questions": []})()

    def fake_run(question):
        state.questions.append(question)
        if isinstance(state.result, Exception):
            raise state.result
        return state.result

    monkeypatch.setattr(main, "run_business_query", fake_run)
    return state


def test_health_still_works():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "DataPilot API"}


def test_query_success(pipeline):
    response = client.post("/query", json={"question": "What is our total revenue?"})
    assert response.status_code == 200
    assert response.json() == SUCCESS.model_dump()
    assert pipeline.questions == ["What is our total revenue?"]


def test_query_response_fields(pipeline):
    body = client.post("/query", json={"question": "Revenue?"}).json()
    assert set(body) == {"question", "sql", "columns", "rows", "row_count", "truncated", "visualization", "insight"}
    assert body["visualization"] == {"type": "kpi", "x_key": None, "y_key": "revenue"}


@pytest.mark.parametrize("payload", [
    {"question": ""},
    {},
    {"question": None},
    {"question": 123},
    {"question": ["What", "is", "revenue"]},
    {"question": "x" * (main.MAX_QUESTION_LENGTH + 1)},
    {"text": "What is our total revenue?"},
])
def test_invalid_request_bodies_get_400(pipeline, payload):
    response = client.post("/query", json=payload)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert pipeline.questions == []


@pytest.mark.parametrize("raw_body", ["not json", "{\"question\": ", ""])
def test_malformed_json_gets_400(pipeline, raw_body):
    response = client.post("/query", content=raw_body, headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert pipeline.questions == []


def test_whitespace_only_question_gets_400(pipeline):
    pipeline.result = QueryServiceError("invalid_question", "Please enter a question.")
    response = client.post("/query", json={"question": "   "})
    assert response.status_code == 400
    assert response.json() == {"error": {"code": "invalid_question", "message": "Please enter a question."}}


@pytest.mark.parametrize("kind, status", [
    ("invalid_question", 400),
    ("unsafe_sql", 400),
    ("query_not_allowed", 400),
    ("query_failed", 422),
    ("rate_limited", 429),
    ("generation_failed", 502),
    ("generation_unavailable", 503),
    ("database_unavailable", 503),
    ("query_timeout", 504),
    ("some_future_kind", 500),
])
def test_service_errors_map_to_http_status(pipeline, kind, status):
    pipeline.result = QueryServiceError(kind, "A safe message.")
    response = client.post("/query", json={"question": "Revenue?"})
    assert response.status_code == status
    assert response.json() == {"error": {"code": kind, "message": "A safe message."}}


def test_unexpected_error_gets_a_generic_500_without_details(pipeline):
    pipeline.result = RuntimeError("password=hunter2 host=db.internal Traceback ...")
    response = TestClient(main.app, raise_server_exceptions=False).post("/query", json={"question": "Revenue?"})
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error", "message": "Something went wrong. Please try again."}}
    assert "hunter2" not in response.text and "Traceback" not in response.text


def test_query_endpoint_is_listed_in_the_openapi_docs():
    paths = client.get("/openapi.json").json()["paths"]
    assert "post" in paths["/query"] and "get" in paths["/health"]


FRONTEND_ORIGIN = "http://localhost:5173"


def test_cors_preflight_allows_the_local_frontend():
    response = client.options("/query", headers={
        "Origin": FRONTEND_ORIGIN,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == FRONTEND_ORIGIN


def test_cors_preflight_rejects_unknown_origins():
    response = client.options("/query", headers={
        "Origin": "https://evil.example.com",
        "Access-Control-Request-Method": "POST",
    })
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_cors_headers_on_success_and_error_responses(pipeline):
    ok = client.post("/query", json={"question": "Revenue?"}, headers={"Origin": FRONTEND_ORIGIN})
    assert ok.headers["access-control-allow-origin"] == FRONTEND_ORIGIN

    pipeline.result = QueryServiceError("generation_unavailable", "A safe message.")
    error = client.post("/query", json={"question": "Revenue?"}, headers={"Origin": FRONTEND_ORIGIN})
    assert error.status_code == 503
    assert error.headers["access-control-allow-origin"] == FRONTEND_ORIGIN


def test_cors_never_allows_every_origin():
    response = client.get("/health", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in response.headers
