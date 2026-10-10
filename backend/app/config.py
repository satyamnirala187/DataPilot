"""Application settings, read from environment variables and the project's .env file."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env lives at the project root: DataPilot/.env
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        # .env may hold values for later phases; ignore anything not defined here.
        extra="ignore",
        # A rejected value (e.g. a too-short DEMO_PASSWORD) must not appear in the startup error.
        hide_input_in_errors=True,
    )

    app_name: str = "DataPilot API"

    # FastAPI's interactive docs (/docs, /redoc, /openapi.json). Off by default: the public API does
    # not need them, so production exposes less. Set ENABLE_API_DOCS=true locally to use them.
    enable_api_docs: bool = False

    # Browser origins allowed to call the API (CORS). Defaults to the local Vite dev server.
    # Override with a JSON list, e.g. CORS_ALLOWED_ORIGINS='["https://datapilot.example.com"]'.
    cors_allowed_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Basic per-client rate limit for POST /query, to protect the Gemini quota: at most
    # rate_limit_requests questions per rate_limit_window_seconds from one client IP.
    # Counted in memory, so each backend process has its own counts (see app/rate_limiter.py).
    # Clients are identified by app/client_ip.py. Behind Cloudflare (Render), set
    # TRUST_CF_CONNECTING_IP=true so the real visitor address in CF-Connecting-IP is used; leave it
    # off anywhere else, because without Cloudflare a client could send that header itself.
    trust_cf_connecting_ip: bool = False
    rate_limit_requests: int = Field(default=5, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)

    # Global safety brake on AI usage: at most this many questions per Pacific day (Gemini's quota
    # day) from all clients together (app/rate_limiter.py DailyLimit). It counts questions, not
    # Gemini requests: one question can use up to 4 (3 SQL attempts + 1 insight), so 5 questions
    # stay within a 20-requests-per-day free tier. Not Gemini's quota, which remains the hard
    # limit. In memory, so a restart resets it.
    global_daily_query_limit: int = Field(default=5, ge=1)

    # Read-only role used for every user query. The admin DATABASE_URL is deliberately not
    # defined here: it is only for the setup scripts in database/. SecretStr keeps the value
    # out of logs and error messages.
    readonly_database_url: SecretStr | None = None
    # Role datapilot_app, for History and Saved Reports only (schema datapilot; created by
    # database/create_app_role.py). It can read and add rows there and cannot see the business
    # tables. Used only by app/history_store.py, never to run generated SQL. Optional: without it
    # answers are returned with analysis_id = null and the History API returns 503.
    app_database_url: SecretStr | None = None
    query_timeout_ms: int = 5000
    max_result_rows: int = 500
    # Most bytes of JSON (columns + rows) one answer may return; larger results are refused, not
    # cut short. Normal answers are far smaller: the benchmark's largest is under 1 KB and a wide
    # 500-row table about 65 KB.
    max_result_bytes: int = Field(default=1_000_000, ge=1)

    # Private demo access (app/auth.py): one shared login, checked only by this backend. Until all
    # three secrets are set, login is unavailable and every /query is refused (fail closed).
    # Never put real values in the repository; on Render they are secret environment variables.
    demo_username: SecretStr | None = Field(default=None, min_length=1, max_length=100)
    demo_password: SecretStr | None = Field(default=None, min_length=16, max_length=200)
    demo_session_secret: SecretStr | None = Field(default=None, min_length=32)  # signs session tokens
    demo_session_minutes: int = Field(default=120, ge=1, le=24 * 60)  # absolute session lifetime

    # Gemini, used to turn questions into SQL. The key is a SecretStr so it never appears in logs.
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.6-flash"


settings = Settings()
