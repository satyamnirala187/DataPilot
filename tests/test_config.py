"""Tests for reading deployment settings from environment variables (backend/app/config.py)."""

import logging
from pathlib import Path

import pytest
from pydantic_settings import SettingsError

from app.config import Settings

ROOT = Path(__file__).resolve().parents[1]


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


def test_groq_model_defaults_to_gpt_oss_120b(monkeypatch):
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    assert Settings(_env_file=None).groq_model == "openai/gpt-oss-120b"


def test_groq_model_comes_from_the_environment(monkeypatch):
    assert settings_from_env(monkeypatch, GROQ_MODEL="groq-test-model").groq_model == "groq-test-model"


def test_groq_key_is_loaded_when_present_and_stays_hidden(monkeypatch, caplog):
    settings = settings_from_env(monkeypatch, GROQ_API_KEY="fake-groq-key-for-test")
    assert settings.groq_api_key.get_secret_value() == "fake-groq-key-for-test"
    logging.getLogger("app").warning("settings: %s %s", settings, settings.groq_api_key)
    for text in (repr(settings), str(settings), settings.model_dump_json(), caplog.text):
        assert "fake-groq-key-for-test" not in text


@pytest.mark.parametrize("value", [None, "", "   "])
def test_settings_work_without_a_groq_key(monkeypatch, value):
    # Groq is optional for now: no key (or the empty line from .env.example) means "not configured".
    if value is None:
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
    else:
        monkeypatch.setenv("GROQ_API_KEY", value)
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    settings = Settings(_env_file=None)
    assert settings.groq_api_key is None
    assert settings.groq_model == "openai/gpt-oss-120b"


def test_tests_never_see_the_real_groq_key():
    from app.config import settings  # the app's settings, loaded from .env at import
    assert settings.groq_api_key is None  # blanked by the autouse fixture in conftest.py


def test_groq_settings_are_never_offered_to_the_frontend():
    example = (ROOT / ".env.example").read_text()
    assert "GROQ_API_KEY=\n" in example  # documented, never with a value
    frontend = [*(ROOT / "frontend" / "src").rglob("*.js*"), ROOT / "frontend" / ".env.example"]
    assert not [path.name for path in frontend if "GROQ" in path.read_text()]
