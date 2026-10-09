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
    )

    app_name: str = "DataPilot API"

    # Browser origins allowed to call the API (CORS). Defaults to the local Vite dev server.
    # Override with a JSON list, e.g. CORS_ALLOWED_ORIGINS='["https://datapilot.example.com"]'.
    cors_allowed_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Basic per-client rate limit for POST /query, to protect the Gemini quota: at most
    # rate_limit_requests questions per rate_limit_window_seconds from one client IP.
    # Counted in memory, so each backend process has its own counts (see app/rate_limiter.py).
    # Behind a reverse proxy, run uvicorn with --proxy-headers so the IP is the real client's.
    rate_limit_requests: int = Field(default=5, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)

    # Read-only role used for every user query. The admin DATABASE_URL is deliberately not
    # defined here: it is only for the setup scripts in database/. SecretStr keeps the value
    # out of logs and error messages.
    readonly_database_url: SecretStr | None = None
    query_timeout_ms: int = 5000
    max_result_rows: int = 500

    # Gemini, used to turn questions into SQL. The key is a SecretStr so it never appears in logs.
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.8-flash"


settings = Settings()
