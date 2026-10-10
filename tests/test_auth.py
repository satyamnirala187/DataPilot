"""Tests for the private demo access gate (backend/app/auth.py and the /auth routes).

These remove the test-only `signed_in` override from conftest.py and exercise the real check.
Credentials and secrets here are obvious fakes; a fake clock replaces real time. No Gemini calls.
"""

import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app import auth, main
from app.config import Settings, settings
from app.query_service import QueryResponse
from app.rate_limiter import DailyLimit, RateLimiter

USER = "demo-user-fake"
PASSWORD = "fake-password-for-tests-only"
SECRET = "fake-signing-secret-for-tests-0123456789"
START = 1_800_000_000.0
OK = QueryResponse(question="q", sql="SELECT 1 LIMIT 501", columns=["n"], rows=[[1]], row_count=1,
                   truncated=False, visualization={"type": "kpi", "y_key": "n"}, insight=None)
UNAUTHORIZED = {"error": {"code": "unauthorized", "message": "Please log in to use DataPilot."}}
INVALID = {"error": {"code": "invalid_credentials", "message": "Incorrect username or password."}}


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def pipeline_calls(monkeypatch):
    calls = []

    def pipeline(question, **_):
        calls.append(question)
        return OK

    monkeypatch.setattr(main, "run_business_query", pipeline)
    return calls


@pytest.fixture(autouse=True)
def real_auth(monkeypatch, clock, pipeline_calls):
    """The real login gate with fake credentials: undo conftest's signed_in override."""
    main.app.dependency_overrides.pop(auth.require_session, None)
    monkeypatch.setattr(settings, "demo_username", SecretStr(USER))
    monkeypatch.setattr(settings, "demo_password", SecretStr(PASSWORD))
    monkeypatch.setattr(settings, "demo_session_secret", SecretStr(SECRET))
    monkeypatch.setattr(settings, "demo_session_minutes", 120)
    monkeypatch.setattr(auth, "clock", clock)
    monkeypatch.setattr(auth, "revoked", auth.RevokedSessions())
    monkeypatch.setattr(main, "login_limiter", RateLimiter(auth.LOGIN_ATTEMPTS, auth.LOGIN_WINDOW_SECONDS, clock=clock))
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(max_requests=100, window_seconds=60))


@pytest.fixture
def client():
    return TestClient(main.app)


def login(client, username=USER, password=PASSWORD):
    return client.post("/auth/login", json={"username": username, "password": password})


def token_for(client):
    response = login(client)
    assert response.status_code == 200
    return response.json()["token"]


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def ask(client, headers=None):
    return client.post("/query", json={"question": "What is our total revenue?"}, headers=headers or {})


def summaries(caplog):
    return [dict(p.split("=", 1) for p in r.getMessage().split()) for r in caplog.records if r.name == "app.request_log"]


# --- Tokens -----------------------------------------------------------------------------------

def test_issued_token_verifies_and_has_the_documented_shape():
    token, session = auth.issue_token()
    version, expiry, session_id, signature = token.split(".")
    assert version == "v1" and int(expiry) == session.expires_at == int(START) + 120 * 60
    assert len(session_id) == 22 and len(signature) == 43
    assert auth.verify_token(token) == session
    assert USER not in token and PASSWORD not in token


def test_every_token_has_its_own_session_id():
    assert auth.issue_token()[1].session_id != auth.issue_token()[1].session_id


def tampered(token, index, value):
    parts = token.split(".")
    parts[index] = value
    return ".".join(parts)


@pytest.mark.parametrize("mutate", [
    lambda t: tampered(t, 1, str(int(t.split(".")[1]) + 3600)),  # longer expiry
    lambda t: tampered(t, 2, "A" * 22),  # another session id
    lambda t: tampered(t, 3, "A" * 43),  # forged signature
    lambda t: tampered(t, 0, "v2"),  # unknown version
    lambda t: t + "x",
    lambda t: t.replace(".", ":"),
    lambda t: "",
    lambda t: "v1...",
    lambda t: "not-a-token",
    lambda t: t + "." + t,
])
def test_tampered_or_malformed_tokens_are_invalid(mutate):
    token, _ = auth.issue_token()
    with pytest.raises(auth.AuthError) as caught:
        auth.verify_token(mutate(token))
    assert caught.value.kind == "invalid_token"


def test_a_token_signed_with_another_secret_is_invalid(monkeypatch):
    token, _ = auth.issue_token()
    monkeypatch.setattr(settings, "demo_session_secret", SecretStr("another-signing-secret-0123456789abcdef"))
    with pytest.raises(auth.AuthError) as caught:
        auth.verify_token(token)
    assert caught.value.kind == "invalid_token"


def test_tokens_expire_at_the_absolute_expiry(clock):
    token, session = auth.issue_token()
    clock.now = session.expires_at - 1
    assert auth.verify_token(token) == session
    clock.now = session.expires_at
    with pytest.raises(auth.AuthError) as caught:
        auth.verify_token(token)
    assert caught.value.kind == "expired_token"


def test_revoked_tokens_are_refused_and_revocations_are_pruned(clock):
    token, session = auth.issue_token()
    auth.revoked.revoke(session)
    with pytest.raises(auth.AuthError) as caught:
        auth.verify_token(token)
    assert caught.value.kind == "revoked_token"
    clock.now = session.expires_at + 1
    assert not auth.revoked.is_revoked(session.session_id) and auth.revoked._expiry_by_id == {}


def test_revocation_memory_is_bounded(monkeypatch):
    monkeypatch.setattr(auth.RevokedSessions, "MAX_ENTRIES", 3)
    store = auth.RevokedSessions()
    sessions = [auth.Session(f"s{n}", int(START) + 100 + n) for n in range(5)]
    for session in sessions:
        store.revoke(session)
    assert len(store._expiry_by_id) == 3
    assert store.is_revoked("s4")  # the newest revocation is kept


# --- Login --------------------------------------------------------------------------------------

def test_valid_credentials_return_a_token_and_its_expiry(client):
    response = login(client)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"token", "expires_at"} and body["expires_at"].endswith("Z")
    assert auth.verify_token(body["token"])


def test_wrong_username_and_wrong_password_get_the_identical_response(client):
    wrong_user, wrong_password = login(client, username="someone-else"), login(client, password="wrong-password-123456")
    both_wrong = login(client, username="x", password="y")
    for response in (wrong_user, wrong_password, both_wrong):
        assert response.status_code == 401 and response.json() == INVALID
    assert wrong_user.text == wrong_password.text == both_wrong.text


@pytest.mark.parametrize("body", [
    {"username": USER, "password": PASSWORD, "remember": True},  # extra field
    {"username": USER},
    {"username": "u" * 101, "password": PASSWORD},
    {"username": USER, "password": "p" * 201},
    {"username": "", "password": PASSWORD},
    {"username": USER, "password": 12345678901234567},
])
def test_malformed_login_requests_are_rejected_without_echoing_them(client, body):
    response = client.post("/auth/login", json=body)
    assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_request"
    assert PASSWORD not in response.text and USER not in response.text


@pytest.mark.parametrize("missing", ["demo_username", "demo_password", "demo_session_secret"])
def test_login_is_unavailable_until_configured(client, monkeypatch, missing):
    monkeypatch.setattr(settings, missing, None)
    response = login(client)
    assert response.status_code == 503 and response.json()["error"]["code"] == "login_unavailable"


def test_sixth_login_attempt_in_fifteen_minutes_is_refused(client, clock):
    statuses = [login(client, password="wrong-password-123456").status_code for _ in range(5)]
    sixth = login(client)  # even with the right password
    assert statuses == [401] * 5
    assert sixth.status_code == 429 and sixth.json()["error"]["code"] == "too_many_login_attempts"
    assert sixth.headers["retry-after"] == str(15 * 60)
    clock.now += 15 * 60
    assert login(client).status_code == 200


def test_successful_logins_count_towards_the_limit(client):
    assert [login(client).status_code for _ in range(6)] == [200, 200, 200, 200, 200, 429]


def test_login_limit_is_per_client(client):
    for _ in range(5):
        login(client, password="wrong-password-123456")
    other = TestClient(main.app, client=("198.51.100.7", 5000))
    assert login(client).status_code == 429 and login(other).status_code == 200


def test_login_never_calls_the_query_pipeline_or_uses_the_query_limits(client, pipeline_calls, monkeypatch):
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(max_requests=1, window_seconds=60))
    monkeypatch.setattr(main, "daily_limit", DailyLimit(1))
    login(client)
    login(client, password="wrong-password-123456")
    assert pipeline_calls == []
    assert ask(client, bearer(token_for(client))).status_code == 200  # both query limits were untouched


# --- /query -------------------------------------------------------------------------------------

@pytest.mark.parametrize("headers, cause", [
    ({}, "missing_token"),
    ({"Authorization": ""}, "missing_token"),
    ({"Authorization": "Basic ZGVtbzpkZW1v"}, "invalid_token"),
    ({"Authorization": "Bearer"}, "invalid_token"),
    ({"Authorization": "Bearer not-a-token"}, "invalid_token"),
    ({"Authorization": "Token v1.1.a.b"}, "invalid_token"),
])
def test_query_without_a_valid_token_is_refused(client, pipeline_calls, caplog, headers, cause):
    caplog.set_level(logging.INFO)
    response = ask(client, headers)
    assert response.status_code == 401 and response.json() == UNAUTHORIZED
    assert response.headers["www-authenticate"] == "Bearer"
    assert pipeline_calls == []
    [entry] = summaries(caplog)
    assert (entry["status"], entry["error_kind"], entry["stage"], entry["cause"]) == ("401", "unauthorized", "auth", cause)
    assert entry["gemini_sql_attempts"] == "0"


def test_query_with_an_expired_token_is_refused(client, clock, pipeline_calls, caplog):
    caplog.set_level(logging.INFO)
    token = token_for(client)
    clock.now += 120 * 60
    assert ask(client, bearer(token)).status_code == 401 and pipeline_calls == []
    assert summaries(caplog)[-1]["cause"] == "expired_token"


def test_query_with_a_revoked_token_is_refused(client, pipeline_calls, caplog):
    caplog.set_level(logging.INFO)
    token = token_for(client)
    assert client.post("/auth/logout", headers=bearer(token)).status_code == 204
    assert ask(client, bearer(token)).status_code == 401 and pipeline_calls == []
    assert summaries(caplog)[-1]["cause"] == "revoked_token"


def test_query_is_refused_when_auth_is_not_configured(client, monkeypatch, pipeline_calls):
    token = token_for(client)
    monkeypatch.setattr(settings, "demo_session_secret", None)
    assert ask(client, bearer(token)).status_code == 401 and pipeline_calls == []


def test_a_valid_token_reaches_the_query_pipeline(client, pipeline_calls):
    response = ask(client, bearer(token_for(client)))
    assert response.status_code == 200 and response.json()["rows"] == [[1]]
    assert pipeline_calls == ["What is our total revenue?"]


def test_refused_queries_use_no_rate_limit_slot_or_daily_unit(client, monkeypatch, pipeline_calls):
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(max_requests=1, window_seconds=60))
    monkeypatch.setattr(main, "daily_limit", DailyLimit(1))
    for _ in range(10):
        assert ask(client).status_code == 401
    assert ask(client, bearer(token_for(client))).status_code == 200
    assert pipeline_calls == ["What is our total revenue?"]


def test_query_limits_still_apply_after_login(client, monkeypatch):
    token = token_for(client)
    monkeypatch.setattr(main, "rate_limiter", RateLimiter(max_requests=1, window_seconds=60))
    assert ask(client, bearer(token)).status_code == 200
    refused = ask(client, bearer(token))
    assert refused.status_code == 429 and refused.json()["error"]["code"] == "too_many_requests"

    monkeypatch.setattr(main, "rate_limiter", RateLimiter(max_requests=100, window_seconds=60))
    monkeypatch.setattr(main, "daily_limit", DailyLimit(1))
    ask(client, bearer(token))
    assert ask(client, bearer(token)).json()["error"]["code"] == "daily_limit_reached"


def test_unauthenticated_queries_never_reach_gemini_or_the_database(client, monkeypatch):
    from app.query_service import run_business_query

    def forbidden(*args):
        pytest.fail("must not be called without a session")

    monkeypatch.setattr(main, "run_business_query", lambda q, **kw: run_business_query(
        q, generate=forbidden, execute=forbidden, summarize=forbidden, **kw))
    assert ask(client).status_code == 401
    assert ask(client, {"Authorization": "Bearer v1.1.x.y"}).status_code == 401


# --- Session and logout ---------------------------------------------------------------------------

def test_session_check_reports_a_valid_token(client):
    response = client.get("/auth/session", headers=bearer(token_for(client)))
    assert response.status_code == 200
    assert response.json()["authenticated"] is True and response.json()["expires_at"].endswith("Z")


def test_session_check_refuses_invalid_expired_and_revoked_tokens(client, clock):
    assert client.get("/auth/session").status_code == 401
    assert client.get("/auth/session", headers=bearer("not-a-token")).status_code == 401
    revoked = token_for(client)
    client.post("/auth/logout", headers=bearer(revoked))
    assert client.get("/auth/session", headers=bearer(revoked)).json() == UNAUTHORIZED
    expired = token_for(client)
    clock.now += 120 * 60
    assert client.get("/auth/session", headers=bearer(expired)).status_code == 401


def test_logout_revokes_and_is_idempotent(client):
    token = token_for(client)
    first = client.post("/auth/logout", headers=bearer(token))
    assert first.status_code == 204 and first.content == b""
    assert client.get("/auth/session", headers=bearer(token)).status_code == 401
    assert client.post("/auth/logout", headers=bearer(token)).status_code == 204
    assert client.post("/auth/logout", headers=bearer("not-a-token")).status_code == 204
    assert client.post("/auth/logout").status_code == 204


def test_logout_ends_only_that_session(client):
    first, second = token_for(client), token_for(client)
    client.post("/auth/logout", headers=bearer(first))
    assert client.get("/auth/session", headers=bearer(second)).status_code == 200


# --- Everything else ----------------------------------------------------------------------------------

def test_health_stays_public_and_docs_stay_off(client):
    assert client.get("/health").status_code == 200
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404


def test_cors_preflight_allows_the_authorization_header_without_credentials(client):
    response = client.options("/query", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization, content-type"})
    assert response.status_code == 200
    assert "authorization" in response.headers["access-control-allow-headers"].lower()
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-credentials" not in response.headers


def test_unknown_origins_still_cannot_send_authorization(client):
    response = client.options("/query", headers={
        "Origin": "https://evil.example.com", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization"})
    assert response.status_code == 400 and "access-control-allow-origin" not in response.headers


def test_login_events_are_logged_without_credentials_or_tokens(client, caplog):
    caplog.set_level(logging.INFO)
    token = token_for(client)
    login(client, password="wrong-password-123456")
    ask(client, bearer(token))
    for _ in range(4):
        login(client)
    logins = [r.getMessage() for r in caplog.records if "event=login" in r.getMessage()]
    outcomes = [line.split("outcome=")[1].split()[0] for line in logins]
    assert outcomes == ["success", "failure", "success", "success", "success", "rate_limited"]
    assert all("request_id=" in line for line in logins)
    for secret in (USER, PASSWORD, SECRET, token, token.split(".")[2], "Bearer", "wrong-password"):
        assert secret not in caplog.text


# --- Configuration ------------------------------------------------------------------------------------

def test_demo_access_settings_default_to_unconfigured(monkeypatch):
    for name in ("DEMO_USERNAME", "DEMO_PASSWORD", "DEMO_SESSION_SECRET", "DEMO_SESSION_MINUTES"):
        monkeypatch.delenv(name, raising=False)
    defaults = Settings(_env_file=None)
    assert (defaults.demo_username, defaults.demo_password, defaults.demo_session_secret) == (None, None, None)
    assert defaults.demo_session_minutes == 120


@pytest.mark.parametrize("name, value", [
    ("DEMO_PASSWORD", "too-short-pw"),  # under 16 characters
    ("DEMO_SESSION_SECRET", "short-secret-under-32"),  # under 32 characters
    ("DEMO_USERNAME", "u" * 101),
    ("DEMO_SESSION_MINUTES", "0"),
])
def test_weak_demo_settings_are_rejected_without_echoing_them(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)
    assert value not in str(caught.value)


def test_demo_secrets_are_hidden_in_repr(monkeypatch):
    monkeypatch.setenv("DEMO_PASSWORD", PASSWORD)
    monkeypatch.setenv("DEMO_SESSION_SECRET", SECRET)
    configured = Settings(_env_file=None)
    assert PASSWORD not in repr(configured) and SECRET not in repr(configured)
