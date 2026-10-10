"""Shared test setup."""

import pytest

from app import auth, main
from app.config import settings
from app.history_store import HistoryResult
from app.rate_limiter import DailyLimit


@pytest.fixture(autouse=True)
def no_real_gemini_key(monkeypatch):
    """Tests never call the real Gemini API: any code path that would build a real client
    sees no key and fails with not_configured. Tests that need a key set a fake one."""
    monkeypatch.setattr(settings, "gemini_api_key", None)


@pytest.fixture(autouse=True)
def fresh_daily_limit(monkeypatch):
    """The global daily cap is process-wide state: give every test its own, generous counter so
    counts never leak between tests. Tests of the cap itself install their own."""
    monkeypatch.setattr(main, "daily_limit", DailyLimit(limit=10_000))


SIGNED_IN = auth.Session(session_id="test-session-override", expires_at=2**31)


@pytest.fixture(autouse=True)
def signed_in():
    """Most tests exercise the query pipeline, not the login gate, so they run as an already
    signed-in user: this overrides the require_session dependency for the test only. Production
    code is unchanged and always requires a session. tests/test_auth.py removes this override
    (its `real_auth` fixture) and tests the real check."""
    main.app.dependency_overrides[auth.require_session] = lambda: SIGNED_IN
    yield
    main.app.dependency_overrides.pop(auth.require_session, None)


class FakeHistory:
    """Stands in for app.history_store.record_analysis in every test, so no test ever writes History
    to the real database (the local .env may hold a real APP_DATABASE_URL). It records each call
    and returns .result: by default "saved" with ANALYSIS_ID. tests/test_history_store.py tests the
    real store against a fake connection."""

    ANALYSIS_ID = "00000000-0000-4000-8000-00000000c0de"

    def __init__(self):
        self.calls = []  # (response, account_id)
        self.result = HistoryResult("saved", analysis_id=self.ANALYSIS_ID)

    def __call__(self, response, *, account_id):
        self.calls.append((response, account_id))
        return self.result


@pytest.fixture(autouse=True)
def fake_history(monkeypatch):
    """The /query endpoint stores History through main.record_analysis; every test gets the fake."""
    fake = FakeHistory()
    monkeypatch.setattr(main, "record_analysis", fake)
    return fake
