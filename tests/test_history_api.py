"""Tests for the History and Saved Reports API (Phase 19, batch 3).

  GET  /history               GET /history/{analysis_id}
  POST /saved-reports         GET /saved-reports         GET /saved-reports/{report_id}

The endpoints use conftest's FakeHistory, an in-memory store with the real store's rules; a few
tests swap in the real store with a failing fake connection. Nothing reaches a database or Gemini.
"""

import logging
from datetime import datetime
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import auth, db_executor, history_store, insight_service, main, nl_to_sql
from app.config import settings
from app.history_store import HistoryUnavailable
from app.rate_limiter import DailyLimit, RateLimiter
from tests.conftest import API_OPERATIONS

client = TestClient(main.app, raise_server_exceptions=False)
FRONTEND_ORIGIN = "http://localhost:5173"
APP_URL = "postgresql://datapilot_app:app-secret-pw@app-db.example.test:5432/postgres"
SECRETS = ("app-secret-pw", "app-db.example.test", "postgresql://", "datapilot_app")
SUMMARY_FIELDS = {"id", "question", "visualization_type", "row_count", "truncated", "created_at", "saved_report_id"}
ANALYSIS_FIELDS = {"id", "question", "sql", "columns", "rows", "row_count", "truncated", "visualization", "insight",
                   "created_at"}
REPORT_SUMMARY_FIELDS = {"id", "analysis_id", "title", "question", "visualization_type", "row_count", "truncated",
                         "saved_at", "analysis_created_at"}


@pytest.fixture(autouse=True)
def isolated_limits(monkeypatch):
    """A fresh History limiter per test; and nothing on the AI side may run or be used."""
    monkeypatch.setattr(main, "app_data_limiter", RateLimiter(max_requests=1000, window_seconds=60))

    def forbidden(*args, **kwargs):
        pytest.fail("History and Saved Reports must never call Gemini, run SQL or use the daily cap")

    for module, name in [(main, "run_business_query"), (nl_to_sql, "generate_sql"), (db_executor, "execute_query"),
                         (insight_service, "generate_insight")]:
        monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setattr(main.daily_limit, "acquire", forbidden)
    monkeypatch.setattr(main.rate_limiter, "check", forbidden)


def endpoints(analysis_id=None, report_id=None):
    """Every new endpoint as (method, path, json)."""
    analysis_id, report_id = analysis_id or uuid4(), report_id or uuid4()
    return [("GET", "/history", None), ("GET", f"/history/{analysis_id}", None),
            ("POST", "/saved-reports", {"analysis_id": str(analysis_id), "title": "A title"}),
            ("GET", "/saved-reports", None), ("GET", f"/saved-reports/{report_id}", None)]


def call(method, path, json=None, **kwargs):
    return client.request(method, path, json=json, **kwargs)


def save(analysis_id, title="Quarterly Revenue"):
    return client.post("/saved-reports", json={"analysis_id": str(analysis_id), "title": title})


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    assert set(response.json()) == {"error"} and response.json()["error"]["code"] == code


def is_iso_timestamp(value):
    return isinstance(value, str) and datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None


# --- Authentication and limits ---------------------------------------------------------------

@pytest.mark.parametrize("method, path, json", endpoints())
def test_every_endpoint_requires_a_session_and_never_reaches_the_store(fake_history, method, path, json):
    main.app.dependency_overrides.pop(auth.require_session, None)
    response = call(method, path, json)
    assert_error(response, 401, "unauthorized")
    assert response.headers["www-authenticate"] == "Bearer"
    assert fake_history.api_calls == []


def test_unauthenticated_requests_use_no_slot_of_the_history_limit(fake_history, monkeypatch):
    monkeypatch.setattr(main, "app_data_limiter", RateLimiter(max_requests=1, window_seconds=60))
    main.app.dependency_overrides.pop(auth.require_session, None)
    for _ in range(3):
        assert client.get("/history").status_code == 401
    main.app.dependency_overrides[auth.require_session] = lambda: auth.Session("signed-in-session-xxxxx", 2**31)
    assert client.get("/history").status_code == 200


def test_the_history_limit_is_per_client_and_says_when_to_retry(fake_history, monkeypatch):
    monkeypatch.setattr(main, "app_data_limiter", RateLimiter(max_requests=2, window_seconds=60))
    for method, path, json in endpoints()[:2]:
        call(method, path, json)
    response = client.get("/saved-reports")
    assert_error(response, 429, "too_many_requests")
    assert int(response.headers["retry-after"]) >= 1
    assert "questions" not in response.json()["error"]["message"]  # not the /query wording
    assert len(fake_history.api_calls) == 2


def test_the_history_limit_is_separate_from_the_ai_limits(fake_history, monkeypatch):
    # The fixture makes the /query limiter and the daily cap fail the test if they are touched.
    for method, path, json in endpoints() * 3:
        call(method, path, json)
    assert main.APP_DATA_REQUESTS_PER_MINUTE == 60


@pytest.mark.parametrize("method", ["DELETE", "PUT", "PATCH"])
@pytest.mark.parametrize("path", ["/history", "/history/{id}", "/saved-reports", "/saved-reports/{id}"])
def test_nothing_can_be_deleted_or_edited(fake_history, method, path):
    assert_error(call(method, path.format(id=uuid4())), 405, "method_not_allowed")
    assert fake_history.api_calls == []


def test_cors_still_allows_only_get_and_post(fake_history):
    preflight = {"Origin": FRONTEND_ORIGIN, "Access-Control-Request-Headers": "authorization"}
    ok = client.options("/saved-reports", headers=preflight | {"Access-Control-Request-Method": "POST"})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == FRONTEND_ORIGIN
    refused = client.options(f"/saved-reports/{uuid4()}", headers=preflight | {"Access-Control-Request-Method": "DELETE"})
    assert refused.status_code == 400
    assert "DELETE" not in ok.headers["access-control-allow-methods"]


# --- GET /history ----------------------------------------------------------------------------

def test_history_lists_summaries_newest_first(fake_history):
    first = fake_history.add_analysis(question="First?")
    second = fake_history.add_analysis(question="Second?", rows=[], row_count=0, insight=None,
                                       visualization={"type": "table"})
    third = fake_history.add_analysis(question="Third?", truncated=True,
                                      visualization={"type": "bar", "x_key": "name", "y_key": "total"},
                                      columns=["name", "total"], rows=[["A", 1]])
    items = client.get("/history").json()["items"]
    assert [item["id"] for item in items] == [str(third.id), str(second.id), str(first.id)]
    assert all(set(item) == SUMMARY_FIELDS for item in items)  # no rows, columns, SQL or insight
    assert items[0] | {"created_at": None} == {
        "id": str(third.id), "question": "Third?", "visualization_type": "bar", "row_count": 1, "truncated": True,
        "created_at": None, "saved_report_id": None}
    assert items[1]["visualization_type"] == "table" and items[1]["row_count"] == 0
    assert all(is_iso_timestamp(item["created_at"]) for item in items)


def test_history_shows_which_analyses_are_saved(fake_history):
    saved, unsaved = fake_history.add_analysis(), fake_history.add_analysis()
    report_id = save(saved.id).json()["id"]
    items = {item["id"]: item for item in client.get("/history").json()["items"]}
    assert items[str(saved.id)]["saved_report_id"] == report_id
    assert items[str(unsaved.id)]["saved_report_id"] is None


def test_history_is_scoped_to_the_sessions_account(fake_history):
    mine = fake_history.add_analysis()
    fake_history.add_analysis(account_id="someone-else")
    assert [item["id"] for item in client.get("/history").json()["items"]] == [str(mine.id)]
    assert fake_history.api_calls == [("list_history", "demo")]


def test_history_survives_a_new_session(fake_history):
    mine = fake_history.add_analysis()
    for session_id in ("first-session-id-xxxxx", "later-session-id-xxxxx"):
        main.app.dependency_overrides[auth.require_session] = lambda s=session_id: auth.Session(s, 2**31)
        assert [item["id"] for item in client.get("/history").json()["items"]] == [str(mine.id)]


@pytest.mark.parametrize("query, expected", [("", 50), ("?limit=1", 1), ("?limit=100", 100), ("?limit=7", 7)])
def test_history_limit(fake_history, query, expected):
    for _ in range(120):
        fake_history.add_analysis()
    assert len(client.get(f"/history{query}").json()["items"]) == expected


@pytest.mark.parametrize("limit", ["0", "-1", "101", "1000", "abc", "1.5", ""])
def test_an_out_of_range_limit_is_rejected(fake_history, limit):
    for path in ("/history", "/saved-reports"):
        assert_error(client.get(f"{path}?limit={limit}"), 400, "invalid_request")
    assert fake_history.api_calls == []


# --- GET /history/{analysis_id} -----------------------------------------------------------------

def test_history_detail_returns_the_full_stored_snapshot(fake_history):
    analysis = fake_history.add_analysis(
        question="Revenue by category?", sql="SELECT c.name, SUM(oi.quantity) AS units FROM categories AS c",
        columns=["name", "units"], rows=[["Books", 10], ["Toys", None]], row_count=2, truncated=True,
        visualization={"type": "bar", "x_key": "name", "y_key": "units"}, insight="Books lead.")
    body = client.get(f"/history/{analysis.id}").json()
    assert set(body) == ANALYSIS_FIELDS | {"saved_report"}
    assert body | {"created_at": None} == {
        "id": str(analysis.id), "question": "Revenue by category?",
        "sql": "SELECT c.name, SUM(oi.quantity) AS units FROM categories AS c", "columns": ["name", "units"],
        "rows": [["Books", 10], ["Toys", None]], "row_count": 2, "truncated": True,
        "visualization": {"type": "bar", "x_key": "name", "y_key": "units"}, "insight": "Books lead.",
        "created_at": None, "saved_report": None}
    assert is_iso_timestamp(body["created_at"])
    assert "account_id" not in body and "snapshot_version" not in body


def test_history_detail_without_insight_or_rows(fake_history):
    analysis = fake_history.add_analysis(rows=[], row_count=0, insight=None, visualization={"type": "table"})
    body = client.get(f"/history/{analysis.id}").json()
    assert (body["rows"], body["row_count"], body["insight"], body["visualization"]) == (
        [], 0, None, {"type": "table", "x_key": None, "y_key": None})


def test_history_detail_includes_its_saved_report(fake_history):
    analysis = fake_history.add_analysis()
    report = save(analysis.id, "Revenue snapshot").json()
    assert client.get(f"/history/{analysis.id}").json()["saved_report"] == {"id": report["id"],
                                                                             "title": "Revenue snapshot"}


def test_unknown_and_other_account_analyses_look_the_same(fake_history):
    other = fake_history.add_analysis(account_id="someone-else")
    unknown, theirs = client.get(f"/history/{uuid4()}"), client.get(f"/history/{other.id}")
    assert_error(unknown, 404, "analysis_not_found")
    assert theirs.status_code == unknown.status_code and theirs.json() == unknown.json()


@pytest.mark.parametrize("bad_id", ["not-a-uuid", "123", "0b6e8f2a-1c3d-4e5f-9a7b", "' OR 1=1 --"])
def test_a_malformed_id_is_an_invalid_request(fake_history, bad_id):
    for path in ("/history", "/saved-reports"):
        assert_error(client.get(f"{path}/{bad_id}"), 400, "invalid_request")
    assert fake_history.api_calls == []


# --- POST /saved-reports ---------------------------------------------------------------------

def test_saving_an_analysis_returns_201_with_the_report(fake_history):
    analysis = fake_history.add_analysis()
    response = save(analysis.id, "Quarterly Revenue")
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "analysis_id", "title", "saved_at"}
    assert UUID(body["id"]) and body["analysis_id"] == str(analysis.id) and body["title"] == "Quarterly Revenue"
    assert is_iso_timestamp(body["saved_at"])
    assert fake_history.api_calls == [("save_report", "demo")]


@pytest.mark.parametrize("title, stored", [
    ("  Quarterly Revenue  ", "Quarterly Revenue"),
    ("\n\tQ3 revenue\n", "Q3 revenue"),
    ("x" * 120, "x" * 120),
    ("  " + "y" * 120 + "  ", "y" * 120),  # the limit applies after trimming
    ("Révenue ₹ by city 📈", "Révenue ₹ by city 📈"),
    ("Two  spaces inside", "Two  spaces inside"),
])
def test_titles_are_trimmed(fake_history, title, stored):
    analysis = fake_history.add_analysis()
    assert save(analysis.id, title).json()["title"] == stored


@pytest.mark.parametrize("title", ["", "   ", "\n\t", "x" * 121, "line\nbreak", "tab\there", "nul\x00byte",
                                   "bell\x07", "escape\x1b[31m", 123, None, ["a"], {"t": "a"}])
def test_invalid_titles_are_rejected(fake_history, title):
    analysis = fake_history.add_analysis()
    response = client.post("/saved-reports", json={"analysis_id": str(analysis.id), "title": title})
    assert_error(response, 400, "invalid_request")
    assert fake_history.reports == {}


@pytest.mark.parametrize("extra", [
    {"rows": [[1]]}, {"columns": ["x"]}, {"sql": "SELECT 1"}, {"question": "q"}, {"insight": "i"},
    {"visualization": {"type": "kpi"}}, {"account_id": "someone-else"}, {"id": str(uuid4())},
])
def test_the_client_can_never_send_report_content(fake_history, extra):
    analysis = fake_history.add_analysis()
    response = client.post("/saved-reports", json={"analysis_id": str(analysis.id), "title": "T"} | extra)
    assert_error(response, 400, "invalid_request")
    assert fake_history.reports == {}


@pytest.mark.parametrize("body", [{"title": "No id"}, {"analysis_id": "not-a-uuid", "title": "T"},
                                  {"analysis_id": 5, "title": "T"}, {}])
def test_a_missing_or_malformed_analysis_id_is_rejected(fake_history, body):
    assert_error(client.post("/saved-reports", json=body), 400, "invalid_request")


def test_saving_an_unknown_or_other_account_analysis_is_404(fake_history):
    other = fake_history.add_analysis(account_id="someone-else")
    unknown, theirs = save(uuid4()), save(other.id)
    assert_error(unknown, 404, "analysis_not_found")
    assert theirs.json() == unknown.json()
    assert fake_history.reports == {}


def test_an_analysis_can_be_saved_only_once(fake_history):
    analysis = fake_history.add_analysis()
    assert save(analysis.id, "First").status_code == 201
    assert_error(save(analysis.id, "Second"), 409, "already_saved")
    assert [report.title for report in fake_history.reports.values()] == ["First"]


# --- GET /saved-reports and /saved-reports/{report_id} -------------------------------------------

def test_saved_reports_list_newest_saved_first(fake_history):
    older, newer = fake_history.add_analysis(question="Older?"), fake_history.add_analysis(question="Newer?")
    save(newer.id, "Saved first")
    save(older.id, "Saved second")
    items = client.get("/saved-reports").json()["items"]
    assert [item["title"] for item in items] == ["Saved second", "Saved first"]
    assert all(set(item) == REPORT_SUMMARY_FIELDS for item in items)  # no rows, columns, SQL or insight
    assert items[0]["analysis_id"] == str(older.id) and items[0]["question"] == "Older?"
    assert all(is_iso_timestamp(item["saved_at"]) and is_iso_timestamp(item["analysis_created_at"]) for item in items)


def test_saved_reports_are_scoped_through_their_analysis(fake_history):
    mine, theirs = fake_history.add_analysis(), fake_history.add_analysis(account_id="someone-else")
    save(mine.id, "Mine")
    fake_history.save_report("someone-else", theirs.id, "Theirs")
    assert [item["title"] for item in client.get("/saved-reports").json()["items"]] == ["Mine"]
    assert client.get("/saved-reports?limit=1").json()["items"][0]["title"] == "Mine"


def test_saved_report_detail_rebuilds_the_full_analysis(fake_history):
    analysis = fake_history.add_analysis(question="Revenue by month?", columns=["month", "revenue"],
                                         rows=[["2026-08-01", 5], ["2026-09-01", 7]], row_count=2,
                                         visualization={"type": "line", "x_key": "month", "y_key": "revenue"},
                                         insight=None)
    report = save(analysis.id, "Monthly revenue").json()
    body = client.get(f"/saved-reports/{report['id']}").json()
    assert set(body) == {"id", "title", "saved_at", "analysis"}
    assert (body["id"], body["title"], body["saved_at"]) == (report["id"], "Monthly revenue", report["saved_at"])
    assert set(body["analysis"]) == ANALYSIS_FIELDS
    # The same stored snapshot History shows, field for field.
    detail = client.get(f"/history/{analysis.id}").json()
    assert body["analysis"] == {field: detail[field] for field in ANALYSIS_FIELDS}


def test_unknown_and_other_account_reports_look_the_same(fake_history):
    theirs = fake_history.add_analysis(account_id="someone-else")
    their_report = fake_history.save_report("someone-else", theirs.id, "Theirs")
    unknown, other = client.get(f"/saved-reports/{uuid4()}"), client.get(f"/saved-reports/{their_report.id}")
    assert_error(unknown, 404, "report_not_found")
    assert other.status_code == 404 and other.json() == unknown.json()


# --- When the History database cannot be used --------------------------------------------------

@pytest.mark.parametrize("kind", ["disabled", "unavailable", "timeout", "query_failed", "invalid_record",
                                  "RuntimeError"])
@pytest.mark.parametrize("method, path, json", endpoints())
def test_store_failures_are_a_safe_503(fake_history, caplog, method, path, json, kind):
    caplog.set_level(logging.INFO)
    fake_history.fail_with = HistoryUnavailable(kind)
    response = call(method, path, json)
    assert_error(response, 503, "history_unavailable")
    assert response.json()["error"]["message"] == "History is not available right now. Please try again later."
    [record] = [r for r in caplog.records if "event=history_unavailable" in r.getMessage()]
    assert record.levelno == logging.WARNING and f"cause={kind}" in record.getMessage()
    assert "endpoint=" in record.getMessage() and response.headers["x-request-id"] in record.getMessage()


@pytest.fixture
def real_store(monkeypatch):
    """The real store functions, with APP_DATABASE_URL set; the test decides what connecting does."""
    for operation in API_OPERATIONS:
        monkeypatch.setattr(main, operation, getattr(history_store, operation))
    monkeypatch.setattr(settings, "app_database_url", SecretStr(APP_URL))
    return lambda connect: monkeypatch.setattr(history_store.psycopg, "connect", connect)


class BrokenConnection:
    """Connects, then fails on the first statement."""

    def __init__(self, error):
        self.error, self.read_only = error, None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def transaction(self):
        return self

    def execute(self, *args):
        raise self.error

    def cursor(self, **kwargs):
        return self


@pytest.mark.parametrize("failure, cause", [
    (psycopg.OperationalError(f"connection to server at app-db.example.test failed: {APP_URL}"), "unavailable"),
    (BrokenConnection(psycopg.errors.QueryCanceled(f"canceling statement; {APP_URL}")), "timeout"),
    (BrokenConnection(psycopg.OperationalError(f"server closed the connection: {APP_URL}")), "unavailable"),
    (BrokenConnection(RuntimeError(f"unexpected with {APP_URL}")), "RuntimeError"),
])
@pytest.mark.parametrize("method, path, json", endpoints())
def test_the_real_store_failing_never_leaks_details(real_store, caplog, method, path, json, failure, cause):
    caplog.set_level(logging.DEBUG)

    def connect(url, **kwargs):
        if isinstance(failure, Exception):
            raise failure
        return failure

    real_store(connect)
    response = call(method, path, json)
    assert_error(response, 503, "history_unavailable")
    assert f"cause={cause}" in caplog.text
    for secret in SECRETS:
        assert secret not in response.text and secret not in caplog.text


@pytest.mark.parametrize("method, path, json", endpoints())
def test_without_app_database_url_the_api_is_unavailable_not_empty(real_store, monkeypatch, caplog, method, path, json):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(settings, "app_database_url", None)
    real_store(lambda *args, **kwargs: pytest.fail("must not connect"))
    response = call(method, path, json)
    assert_error(response, 503, "history_unavailable")
    assert "cause=disabled" in caplog.text


def test_a_failing_store_never_affects_query_history_recording(fake_history, monkeypatch):
    # The API needs the database; POST /query does not. Its non-fatal History contract is unchanged.
    fake_history.fail_with = HistoryUnavailable("unavailable")
    monkeypatch.setattr(main, "run_business_query", lambda q, **kw: main.QueryResponse(
        question=q, sql="SELECT 1", columns=["x"], rows=[[1]], row_count=1, truncated=False,
        visualization={"type": "kpi", "y_key": "x"}, insight=None))
    monkeypatch.setattr(main.daily_limit, "acquire", lambda: None)
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(max_requests=10, window_seconds=60))
    response = client.post("/query", json={"question": "Total?"})
    assert response.status_code == 200 and response.json()["analysis_id"] == fake_history.ANALYSIS_ID
