"""Tests for reading deployment settings from environment variables (backend/app/config.py)."""

import pytest
from pydantic_settings import SettingsError

from app.config import Settings


def settings_from_env(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # environment only, like Render


def test_cors_origins_are_read_as_a_json_list(monkeypatch):
    origins = '["https://datapilot.vercel.app","http://localhost:5173","http://127.0.0.1:5173"]'
    settings = settings_from_env(monkeypatch, CORS_ALLOWED_ORIGINS=origins)
    assert settings.cors_allowed_origins == ["https://datapilot.vercel.app", "http://localhost:5173",
                                             "http://127.0.0.1:5173"]


def test_cors_origins_in_a_non_json_format_fail_at_startup(monkeypatch):
    # A comma-separated value is a likely dashboard mistake; it must stop the app, not allow nothing.
    with pytest.raises(SettingsError):
        settings_from_env(monkeypatch, CORS_ALLOWED_ORIGINS="https://datapilot.vercel.app,http://localhost:5173")


def test_rate_limit_and_model_settings_come_from_the_environment(monkeypatch):
    settings = settings_from_env(monkeypatch, RATE_LIMIT_REQUESTS="10", RATE_LIMIT_WINDOW_SECONDS="120",
                                 GEMINI_MODEL="gemini-test-model")
    assert (settings.rate_limit_requests, settings.rate_limit_window_seconds) == (10, 120)
    assert settings.gemini_model == "gemini-test-model"


def test_secrets_from_the_environment_stay_hidden(monkeypatch):
    settings = settings_from_env(monkeypatch, READONLY_DATABASE_URL="postgresql://app_user:hunter2@db.example/postgres",
                                 GEMINI_API_KEY="fake-key-for-test")
    assert "hunter2" not in repr(settings) and "fake-key-for-test" not in repr(settings)
    assert settings.readonly_database_url.get_secret_value().endswith("@db.example/postgres")
