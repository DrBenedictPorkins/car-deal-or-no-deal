"""Application configuration.

Everything is local-first by default: loopback bind, local SQLite, no LLM egress.
Turning any of that off requires an explicit, named setting.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DEALBENCH_",
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- storage -----------------------------------------------------------
    data_dir: Path = DEFAULT_DATA_DIR
    database_url: str | None = None

    # --- server ------------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8756
    # Binding off-loopback is a deliberate act and requires a shared token.
    allow_non_loopback_bind: bool = False
    api_token: str | None = None

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # --- negotiation defaults (overridable per buyer profile) --------------
    follow_up_after_hours: int = 48
    no_response_after_days: int = 7
    deadline_warning_hours: int = 24

    # --- LLM ---------------------------------------------------------------
    # "null" performs no network I/O at all. Phase 1 ships with it as default.
    llm_provider: str = "null"
    llm_model: str | None = None
    llm_enabled: bool = False
    # Off by default: storing prompts means storing dealer correspondence twice.
    llm_store_payloads: bool = False

    @field_validator("data_dir", mode="after")
    @classmethod
    def _ensure_dir(cls, value: Path) -> Path:
        value.mkdir(parents=True, exist_ok=True)
        (value / "blobs").mkdir(parents=True, exist_ok=True)
        return value

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+pysqlite:///{self.data_dir / 'dealbench.sqlite3'}"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
