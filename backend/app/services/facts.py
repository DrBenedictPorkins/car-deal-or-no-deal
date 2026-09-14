"""Append-only fact store.

The rule the brief cares most about: a fact is never overwritten. Recording a new value
for an attribute supersedes the old row and links the two together, so "what did they
originally say?" and "why does the system believe this?" are both answerable forever.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import ExtractionMethod, FactStatus, Party, SubjectType
from app.models import Fact, Interaction
from app.models.base import utcnow


def _display(
    value_text: str | None,
    value_number: float | None,
    value_bool: bool | None,
    value_datetime: datetime | None,
    unit: str | None,
) -> str:
    if value_bool is not None:
        return "Yes" if value_bool else "No"
    if value_number is not None:
        if unit == "cents":
            whole, frac = divmod(abs(int(value_number)), 100)
            return f"${whole:,}.{frac:02d}"
        return f"{value_number:g}" + (f" {unit}" if unit else "")
    if value_datetime is not None:
        return value_datetime.strftime("%Y-%m-%d %H:%M")
    return value_text or "—"


def _same_value(fact: Fact, **kwargs) -> bool:
    return all(
        getattr(fact, key) == kwargs.get(key)
        for key in ("value_text", "value_number", "value_bool", "value_datetime")
    )


def record(
    db: Session,
    *,
    subject_type: SubjectType | str,
    subject_id: int,
    attribute: str,
    dealer_id: int | None = None,
    value_text: str | None = None,
    value_number: float | None = None,
    value_bool: bool | None = None,
    value_datetime: datetime | None = None,
    value_unit: str | None = None,
    interaction_id: int | None = None,
    document_id: int | None = None,
    transcript_segment_id: int | None = None,
    offer_id: int | None = None,
    llm_run_id: int | None = None,
    quote: str | None = None,
    quote_start: int | None = None,
    quote_end: int | None = None,
    method: ExtractionMethod | str = ExtractionMethod.MANUAL,
    confidence: float | None = None,
    asserted_by_party: Party | str = Party.DEALER,
    observed_at: datetime | None = None,
    status: FactStatus | str = FactStatus.CURRENT,
) -> Fact:
    """Write a fact, superseding any current fact for the same subject+attribute.

    Re-recording an identical value from the same source is a no-op, so replaying an
    ingest does not churn the history.
    """
    existing = db.scalars(
        select(Fact)
        .where(
            Fact.subject_type == str(subject_type),
            Fact.subject_id == subject_id,
            Fact.attribute == attribute,
            Fact.status == FactStatus.CURRENT,
        )
        .order_by(Fact.created_at.desc())
    ).all()

    values = {
        "value_text": value_text,
        "value_number": value_number,
        "value_bool": value_bool,
        "value_datetime": value_datetime,
    }

    for prior in existing:
        if _same_value(prior, **values) and prior.interaction_id == interaction_id:
            return prior  # identical assertion from the identical source

    fact = Fact(
        subject_type=str(subject_type),
        subject_id=subject_id,
        attribute=attribute,
        dealer_id=dealer_id,
        value_text=value_text,
        value_number=value_number,
        value_bool=value_bool,
        value_datetime=value_datetime,
        value_unit=value_unit,
        display_value=_display(value_text, value_number, value_bool, value_datetime, value_unit),
        status=str(status),
        method=str(method),
        confidence=confidence,
        interaction_id=interaction_id,
        document_id=document_id,
        transcript_segment_id=transcript_segment_id,
        offer_id=offer_id,
        llm_run_id=llm_run_id,
        quote=quote,
        quote_start=quote_start,
        quote_end=quote_end,
        asserted_by_party=str(asserted_by_party),
        observed_at=observed_at or utcnow(),
    )
    db.add(fact)
    db.flush()

    if str(status) == FactStatus.CURRENT:
        for prior in existing:
            prior.status = FactStatus.SUPERSEDED
            prior.superseded_by_id = fact.id
        db.flush()
    return fact


def current(
    db: Session, subject_type: SubjectType | str, subject_id: int, attribute: str | None = None
) -> list[Fact]:
    q = select(Fact).where(
        Fact.subject_type == str(subject_type),
        Fact.subject_id == subject_id,
        Fact.status == FactStatus.CURRENT,
    )
    if attribute:
        q = q.where(Fact.attribute == attribute)
    return list(db.scalars(q.order_by(Fact.attribute)).all())


def history(
    db: Session, subject_type: SubjectType | str, subject_id: int, attribute: str
) -> list[Fact]:
    """Full chain, newest first. Superseded rows are never deleted."""
    return list(
        db.scalars(
            select(Fact)
            .where(
                Fact.subject_type == str(subject_type),
                Fact.subject_id == subject_id,
                Fact.attribute == attribute,
            )
            .order_by(Fact.created_at.desc())
        ).all()
    )


def for_dealer(db: Session, dealer_id: int, *, include_superseded: bool = False) -> list[Fact]:
    q = select(Fact).where(Fact.dealer_id == dealer_id)
    if not include_superseded:
        q = q.where(Fact.status == FactStatus.CURRENT)
    return list(db.scalars(q.order_by(Fact.attribute, Fact.created_at.desc())).all())


@dataclass
class Provenance:
    fact: Fact
    interaction: Interaction | None
    superseded: list[Fact]


def provenance(db: Session, fact_id: int) -> Provenance | None:
    """Everything needed to answer "why does the system believe this?"."""
    fact = db.get(Fact, fact_id)
    if fact is None:
        return None
    interaction = (
        db.get(Interaction, fact.interaction_id) if fact.interaction_id is not None else None
    )
    chain = history(db, fact.subject_type, fact.subject_id, fact.attribute)
    return Provenance(
        fact=fact,
        interaction=interaction,
        superseded=[f for f in chain if f.id != fact.id],
    )
