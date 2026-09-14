from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.enums import (
    CommitmentStatus,
    ContradictionStatus,
    ExtractionMethod,
    FactStatus,
    Party,
    QuestionStatus,
    Severity,
)
from app.models.base import Base, utcnow


class Fact(Base):
    """Append-only provenance record.

    Every non-user-entered assertion the system makes is a row here, carrying the
    interaction it came from and the verbatim quote that supports it. Never UPDATEd
    except to set ``status`` / ``superseded_by_id``.
    """

    __tablename__ = "fact"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    subject_type: Mapped[str] = mapped_column(String(20), nullable=False)
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False)
    attribute: Mapped[str] = mapped_column(String(120), nullable=False)

    value_text: Mapped[str | None] = mapped_column(Text)
    value_number: Mapped[float | None] = mapped_column(Float)
    value_bool: Mapped[bool | None] = mapped_column(Boolean)
    value_datetime: Mapped[datetime | None] = mapped_column(DateTime)
    value_unit: Mapped[str | None] = mapped_column(String(20))
    display_value: Mapped[str | None] = mapped_column(String(300))

    status: Mapped[str] = mapped_column(String(20), default=FactStatus.CURRENT, nullable=False)
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("fact.id", ondelete="SET NULL"))

    method: Mapped[str] = mapped_column(String(20), default=ExtractionMethod.MANUAL)
    confidence: Mapped[float | None] = mapped_column(Float)

    dealer_id: Mapped[int | None] = mapped_column(ForeignKey("dealer.id", ondelete="CASCADE"))
    interaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("interaction.id", ondelete="SET NULL")
    )
    document_id: Mapped[int | None] = mapped_column(ForeignKey("document.id", ondelete="SET NULL"))
    transcript_segment_id: Mapped[int | None] = mapped_column(
        ForeignKey("transcript_segment.id", ondelete="SET NULL")
    )
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("offer.id", ondelete="SET NULL"))
    llm_run_id: Mapped[int | None] = mapped_column(ForeignKey("llm_run.id", ondelete="SET NULL"))

    quote: Mapped[str | None] = mapped_column(Text)
    quote_start: Mapped[int | None] = mapped_column(Integer)
    quote_end: Mapped[int | None] = mapped_column(Integer)

    asserted_by_party: Mapped[str] = mapped_column(String(20), default=Party.DEALER)

    observed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_fact_subject", "subject_type", "subject_id", "attribute", "status"),
        Index("ix_fact_dealer", "dealer_id", "attribute"),
    )


class StateTransition(Base):
    """Append-only negotiation state log. Dealer.state_code caches the latest row."""

    __tablename__ = "state_transition"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dealer_id: Mapped[int] = mapped_column(
        ForeignKey("dealer.id", ondelete="CASCADE"), nullable=False
    )
    from_state: Mapped[str | None] = mapped_column(String(40))
    to_state: Mapped[str] = mapped_column(String(40), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(60))
    reason_text: Mapped[str | None] = mapped_column(Text)
    triggered_by: Mapped[str] = mapped_column(String(20), default="RULE")
    rule_id: Mapped[str | None] = mapped_column(String(60))
    interaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("interaction.id", ondelete="SET NULL")
    )
    # Records what the engine *would* have done when the user has pinned a state.
    was_suppressed_by_pin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (Index("ix_transition_dealer", "dealer_id", "created_at"),)


class Question(Base):
    __tablename__ = "question"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dealer_id: Mapped[int] = mapped_column(
        ForeignKey("dealer.id", ondelete="CASCADE"), nullable=False
    )
    interaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("interaction.id", ondelete="SET NULL")
    )
    asked_by: Mapped[str] = mapped_column(String(20), default=Party.BUYER, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    topic: Mapped[str | None] = mapped_column(String(80))
    importance: Mapped[str] = mapped_column(String(20), default=Severity.INFO)

    status: Mapped[str] = mapped_column(String(20), default=QuestionStatus.OPEN, nullable=False)
    answered_interaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("interaction.id", ondelete="SET NULL")
    )
    answer_summary: Mapped[str | None] = mapped_column(Text)

    asked_at: Mapped[datetime | None] = mapped_column(DateTime)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (Index("ix_question_dealer_status", "dealer_id", "status"),)


class Commitment(Base):
    """"I'll send the OTD tonight" as an object with a due time, so a broken promise
    becomes detectable rather than forgotten."""

    __tablename__ = "commitment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dealer_id: Mapped[int] = mapped_column(
        ForeignKey("dealer.id", ondelete="CASCADE"), nullable=False
    )
    party: Mapped[str] = mapped_column(String(20), default=Party.DEALER, nullable=False)
    interaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("interaction.id", ondelete="SET NULL")
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default=CommitmentStatus.OPEN, nullable=False)
    evidence_interaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("interaction.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (Index("ix_commitment_dealer_status", "dealer_id", "status"),)


class Contradiction(Base):
    """Both sides are always retained. The system never decides which is true."""

    __tablename__ = "contradiction"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dealer_id: Mapped[int] = mapped_column(
        ForeignKey("dealer.id", ondelete="CASCADE"), nullable=False
    )
    attribute: Mapped[str | None] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default=Severity.WARNING)

    fact_a_id: Mapped[int | None] = mapped_column(ForeignKey("fact.id", ondelete="SET NULL"))
    fact_b_id: Mapped[int | None] = mapped_column(ForeignKey("fact.id", ondelete="SET NULL"))
    interaction_a_id: Mapped[int | None] = mapped_column(
        ForeignKey("interaction.id", ondelete="SET NULL")
    )
    interaction_b_id: Mapped[int | None] = mapped_column(
        ForeignKey("interaction.id", ondelete="SET NULL")
    )

    summary: Mapped[str] = mapped_column(Text, nullable=False)
    detail_a: Mapped[str | None] = mapped_column(Text)
    detail_b: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(
        String(20), default=ContradictionStatus.OPEN, nullable=False
    )
    resolution_note: Mapped[str | None] = mapped_column(Text)
    detected_by: Mapped[str] = mapped_column(String(20), default="RULE")
    # Stable identity so re-running detection updates rather than duplicates.
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_contradiction_dedupe", "dedupe_key", unique=True),
        Index("ix_contradiction_dealer", "dealer_id", "status"),
    )


class BehaviorSignal(Base):
    """Observed dealer behavior. Scores are a deterministic function of these rows,
    and the UI always shows the rows alongside the score."""

    __tablename__ = "behavior_signal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dealer_id: Mapped[int] = mapped_column(
        ForeignKey("dealer.id", ondelete="CASCADE"), nullable=False
    )
    dimension: Mapped[str] = mapped_column(String(30), nullable=False)
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    polarity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    interaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("interaction.id", ondelete="SET NULL")
    )
    observed_at: Mapped[datetime | None] = mapped_column(DateTime)
    detail: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), default="RULE")
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_signal_dealer", "dealer_id", "dimension"),
        Index("ix_signal_dedupe", "dedupe_key", unique=True),
    )
