"""Shared test setup."""

import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def no_real_gemini_key(monkeypatch):
    """Tests never call the real Gemini API: any code path that would build a real client
    sees no key and fails with not_configured. Tests that need a key set a fake one."""
    monkeypatch.setattr(settings, "gemini_api_key", None)
