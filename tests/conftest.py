"""Shared test setup."""

import pytest

from app import auth, main
from app.config import settings
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
