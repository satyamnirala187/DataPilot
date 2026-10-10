"""Shared test setup."""

import pytest

from app import main
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
