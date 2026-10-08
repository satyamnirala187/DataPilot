"""Application settings, read from environment variables and the project's .env file."""

from pathlib import Path

from pydantic import SecretStr
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

    # Read-only role used for every user query. The admin DATABASE_URL is deliberately not
    # defined here: it is only for the setup scripts in database/. SecretStr keeps the value
    # out of logs and error messages.
    readonly_database_url: SecretStr | None = None
    query_timeout_ms: int = 5000
    max_result_rows: int = 500


settings = Settings()
