from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.enums import Channel, DraftStatus, Severity
from app.models.base import Base, TimestampMixin, utcnow


class DraftMessage(Base, TimestampMixin):
    """Generated reply awaiting the user's EDIT / APPROVE / DISCARD.

    ``SENT`` exists in the state machine so autonomous sending is an additive change
    later, but nothing in this codebase writes it and no send scope is requested.
    """

    __tablename__ = "draft_message"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dealer_id: Mapped[int] = mapped_column(
        ForeignKey("dealer.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contact.id", ondelete="SET NULL"))
    channel: Mapped[str] = mapped_column(String(20), default=Channel.EMAIL)
    in_reply_to_interaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("interaction.id", ondelete="SET NULL")
    )

    subject: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Why this draft says what it says — shown next to the draft, never hidden.
    rationale: Mapped[str | None] = mapped_column(Text)
    strategy_notes: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(20), default=DraftStatus.DRAFT, nullable=False)
    edited_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    generated_by: Mapped[str] = mapped_column(String(20), default="RULE")
    llm_run_id: Mapped[int | None] = mapped_column(ForeignKey("llm_run.id", ondelete="SET NULL"))

    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    sent_interaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("interaction.id", ondelete="SET NULL")
    )

    __table_args__ = (Index("ix_draft_dealer_status", "dealer_id", "status"),)


class Notification(Base):
    __tablename__ = "notification"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dealer_id: Mapped[int | None] = mapped_column(ForeignKey("dealer.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default=Severity.INFO)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    # Uniqueness is how "do not over-notify" becomes a constraint, not a hope.
    dedupe_key: Mapped[str] = mapped_column(String(300), nullable=False)
    context_json: Mapped[str | None] = mapped_column(Text)

    interaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("interaction.id", ondelete="SET NULL")
    )
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("offer.id", ondelete="SET NULL"))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        Index("ix_notification_dedupe", "dedupe_key", unique=True),
        Index("ix_notification_unread", "read_at", "created_at"),
    )


class LLMRun(Base):
    """Audit of every model call. Payload columns only populated when explicitly
    enabled, because storing prompts means storing dealer correspondence twice."""

    __tablename__ = "llm_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str | None] = mapped_column(String(120))
    purpose: Mapped[str] = mapped_column(String(60), nullable=False)
    interaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("interaction.id", ondelete="SET NULL")
    )
    dealer_id: Mapped[int | None] = mapped_column(ForeignKey("dealer.id", ondelete="SET NULL"))

    prompt_hash: Mapped[str | None] = mapped_column(String(64))
    schema_name: Mapped[str | None] = mapped_column(String(120))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="OK")
    error: Mapped[str | None] = mapped_column(Text)

    prompt_text: Mapped[str | None] = mapped_column(Text)
    response_text: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class SyncState(Base):
    """Per-provider ingestion cursor. Populated in Phase 2."""

    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    account_email: Mapped[str | None] = mapped_column(String(320))
    last_history_id: Mapped[str | None] = mapped_column(String(80))
    last_full_sync_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_incremental_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="IDLE")
    error: Mapped[str | None] = mapped_column(Text)
    cursor_json: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_sync_provider", "provider", "account_email", unique=True),)
