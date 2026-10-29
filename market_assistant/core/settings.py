"""One place for configuration. Values come from environment variables or .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class ProviderSpec(BaseModel):
    name: str
    base_url: str
    model: str
    api_key_env: str | None = None
    timeout_s: float = 30.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MA_", extra="ignore")

    db_path: Path = ROOT / "data" / "market.db"
    # Set to a PostgreSQL URL for a read-only role (see data/load_postgres.py) to use Postgres instead of SQLite.
    database_url: str | None = None

    # Any OpenAI-compatible chat endpoint (OpenAI, Gemini's OpenAI API, a local server...).
    # Leave the key empty to run fully offline with the rule-based planner and writer.
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "gemini-2.5-flash"
    llm_timeout_s: float = 30.0
    # Optional provider group for failover, as JSON (see .env.example). Keys come from the named env vars.
    llm_providers: list[ProviderSpec] = []

    max_rows: int = Field(50, ge=1, le=500)
    query_timeout_s: float = Field(2.0, gt=0)
    history_turns: int = Field(6, ge=0, le=20)
    session_ttl_s: int = 1800
    # Save people and conversations (SQLite file). Off when unset; the local app keeps it next to its settings.
    history_db: Path | None = None
    # Key for the admin endpoints (people and conversations). The admin API is off when empty.
    admin_key: SecretStr = SecretStr("")
    rate_limit_per_minute: int = Field(30, ge=1)
    allowed_origins: list[str] = ["*"]
    log_level: str = "INFO"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key.get_secret_value()) or bool(self.llm_providers)


@lru_cache
def get_settings() -> Settings:
    return Settings()
