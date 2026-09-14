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

    # --- ingestion ----------------------------------------------------------
    # UNIT   — no transport at all; stored fixture messages only.
    # REPLAY — historical .eml messages replayed chronologically.
    # GMAIL  — the real Gmail API against a dedicated account.
    # Business logic is identical in all three; only the source differs.
    ingest_mode: str = "UNIT"

    gmail_client_secret_file: Path | None = None
    gmail_token_file: Path | None = None
    gmail_account: str | None = None
    # Read-only until sending is explicitly turned on. gmail.send is never added
    # implicitly — it takes both send_enabled and an allowlist.
    gmail_readonly_scopes: list[str] = Field(
        default_factory=lambda: ["https://www.googleapis.com/auth/gmail.readonly"]
    )
    gmail_send_scope: str = "https://www.googleapis.com/auth/gmail.send"
    # Bound the historical import so a first sync cannot hoover up a whole mailbox.
    gmail_import_query: str = ""
    gmail_import_max_messages: int = 2000
    # Writing fixture messages into a mailbox needs the gmail.insert scope and an
    # explicit opt-in. Never used against anything but a dedicated test account.
    gmail_allow_insert: bool = False
    gmail_insert_scope: str = "https://www.googleapis.com/auth/gmail.insert"

    # --- outbound safety ----------------------------------------------------
    # Three independent locks, all of which must be open before a byte is sent:
    # the transport must be enabled, safe mode must allow the recipient, and a
    # human must have approved the specific draft.
    send_enabled: bool = False
    send_safe_mode: bool = True
    send_allowlist: list[str] = Field(default_factory=list)

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

    @property
    def token_path(self) -> Path:
        return self.gmail_token_file or (self.data_dir / "gmail_token.json.enc")

    @property
    def key_path(self) -> Path:
        return self.data_dir / "token_key"

    def sending_allowed_to(self, recipient: str) -> tuple[bool, str]:
        """Whether this address may be sent to, and why not if it may not."""
        if not self.send_enabled:
            return False, (
                "Sending is disabled. Set DEALBENCH_SEND_ENABLED=true to turn on the "
                "outbound transport."
            )
        address = _address_of(recipient)
        allowlist = {_address_of(a) for a in self.send_allowlist}
        if self.send_safe_mode and address not in allowlist:
            return False, (
                f"Safe mode is on and {address} is not in DEALBENCH_SEND_ALLOWLIST. "
                f"Allowed: {', '.join(sorted(allowlist)) or '(nothing)'}."
            )
        return True, ""


def _address_of(value: str) -> str:
    """Bare lowercase address from either "Name <a@b>" or "a@b"."""
    value = value.strip()
    if "<" in value and ">" in value:
        value = value[value.index("<") + 1 : value.index(">")]
    return value.strip().lower()


@lru_cache
def get_settings() -> Settings:
    return Settings()
