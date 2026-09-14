from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums import ActorKind
from app.models.base import Base, utcnow

if TYPE_CHECKING:
    from app.models.dealer import Dealer


class Interaction(Base):
    """The fundamental communication object.

    Email is one channel among several — never the base class.
    """

    __tablename__ = "interaction"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dealer_id: Mapped[int | None] = mapped_column(ForeignKey("dealer.id", ondelete="CASCADE"))
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contact.id", ondelete="SET NULL"))
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicle.id", ondelete="SET NULL"))
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaign.id", ondelete="SET NULL"))

    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    subject: Mapped[str | None] = mapped_column(String(500))
    raw_content: Mapped[str | None] = mapped_column(Text)
    normalized_content: Mapped[str | None] = mapped_column(Text)
    # What quoting/signature stripping removed. Preserved, never discarded (A5).
    quoted_content: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)

    actor_kind: Mapped[str] = mapped_column(String(20), default=ActorKind.UNKNOWN)
    classification_reason: Mapped[str | None] = mapped_column(Text)
    is_quote_bearing: Mapped[bool] = mapped_column(Boolean, default=False)

    source_system: Mapped[str] = mapped_column(String(40), default="manual")
    source_identifier: Mapped[str | None] = mapped_column(String(300))
    source_thread_identifier: Mapped[str | None] = mapped_column(String(300))
    source_file: Mapped[str | None] = mapped_column(String(500))
    content_hash: Mapped[str | None] = mapped_column(String(64))

    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    extractor_version: Mapped[str | None] = mapped_column(String(40))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    dealer: Mapped[Dealer | None] = relationship(back_populates="interactions")
    email_source: Mapped[EmailSource | None] = relationship(
        back_populates="interaction", uselist=False, cascade="all, delete-orphan"
    )
    transcript_source: Mapped[TranscriptSource | None] = relationship(
        back_populates="interaction", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        # The two dedupe guards. Both are needed: forwarded mail can arrive twice
        # under different provider ids with identical content.
        UniqueConstraint("source_system", "source_identifier", name="uq_interaction_source"),
        Index("ix_interaction_dealer_time", "dealer_id", "occurred_at"),
        Index("ix_interaction_thread", "source_thread_identifier"),
        Index("ix_interaction_hash", "content_hash"),
        Index("ix_interaction_review", "needs_review"),
    )


class EmailSource(Base):
    """Channel-specific metadata for ``channel = EMAIL``. Populated in Phase 2."""

    __tablename__ = "email_source"

    interaction_id: Mapped[int] = mapped_column(
        ForeignKey("interaction.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(40), default="gmail")
    provider_message_id: Mapped[str | None] = mapped_column(String(200))
    provider_thread_id: Mapped[str | None] = mapped_column(String(200))
    rfc822_message_id: Mapped[str | None] = mapped_column(String(400))
    in_reply_to: Mapped[str | None] = mapped_column(String(400))
    references: Mapped[str | None] = mapped_column(Text)

    from_name: Mapped[str | None] = mapped_column(String(200))
    from_email: Mapped[str | None] = mapped_column(String(320))
    to_emails: Mapped[str | None] = mapped_column(Text)
    cc_emails: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)

    snippet: Mapped[str | None] = mapped_column(Text)
    label_ids: Mapped[str | None] = mapped_column(String(500))
    has_attachments: Mapped[bool] = mapped_column(Boolean, default=False)
    mime_summary: Mapped[str | None] = mapped_column(String(500))
    history_id: Mapped[str | None] = mapped_column(String(80))

    interaction: Mapped[Interaction] = relationship(back_populates="email_source")

    __table_args__ = (
        Index("ix_email_provider_msg", "provider", "provider_message_id", unique=True),
        Index("ix_email_rfc822", "rfc822_message_id"),
    )


class TranscriptSource(Base):
    """Channel-specific metadata for ``channel = CALL``. Populated in Phase 4."""

    __tablename__ = "transcript_source"

    interaction_id: Mapped[int] = mapped_column(
        ForeignKey("interaction.id", ondelete="CASCADE"), primary_key=True
    )
    format: Mapped[str] = mapped_column(String(20), default="TXT")
    original_filename: Mapped[str | None] = mapped_column(String(300))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    speaker_count: Mapped[int | None] = mapped_column(Integer)
    transcription_engine: Mapped[str | None] = mapped_column(String(80))
    transcription_confidence: Mapped[float | None] = mapped_column()
    audio_blob_path: Mapped[str | None] = mapped_column(String(500))
    diarization_available: Mapped[bool] = mapped_column(Boolean, default=False)

    interaction: Mapped[Interaction] = relationship(back_populates="transcript_source")


class TranscriptSegment(Base):
    """Timecoded lines so a fact from a call can cite a moment, not a file."""

    __tablename__ = "transcript_segment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    interaction_id: Mapped[int] = mapped_column(
        ForeignKey("interaction.id", ondelete="CASCADE"), nullable=False
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int | None] = mapped_column(Integer)
    end_ms: Mapped[int | None] = mapped_column(Integer)
    speaker_label: Mapped[str | None] = mapped_column(String(80))
    speaker_party: Mapped[str | None] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("ix_segment_interaction", "interaction_id", "index"),)


class Document(Base):
    """Uploaded worksheets, buyer's orders, screenshots. Original always retained."""

    __tablename__ = "document"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    interaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("interaction.id", ondelete="SET NULL")
    )
    dealer_id: Mapped[int | None] = mapped_column(ForeignKey("dealer.id", ondelete="CASCADE"))
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("offer.id", ondelete="SET NULL"))

    kind: Mapped[str] = mapped_column(String(20), default="OTHER")
    original_filename: Mapped[str | None] = mapped_column(String(300))
    mime_type: Mapped[str | None] = mapped_column(String(120))
    byte_size: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    blob_path: Mapped[str | None] = mapped_column(String(500))
    page_count: Mapped[int | None] = mapped_column(Integer)

    extracted_text: Mapped[str | None] = mapped_column(Text)
    extraction_engine: Mapped[str | None] = mapped_column(String(80))
    extraction_confidence: Mapped[float | None] = mapped_column()
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (Index("ix_document_sha", "sha256"),)
