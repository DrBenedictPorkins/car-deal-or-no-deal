"""Deterministic contradiction detection.

The system never decides which of two conflicting statements is true. It shows both,
with their timestamps and sources, and leaves the judgement to the user — a dealer who
says one thing on the phone and another in writing might be lying, confused, or just
have two employees, and the software cannot tell which.

Detectors here are rule-based and run without an LLM. Phase 3 adds an LLM detector for
semantic conflicts that rules cannot see; its findings land in the same table, tagged
``detected_by = LLM``.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import (
    CommitmentStatus,
    ContradictionKind,
    FactStatus,
    Severity,
)
from app.models import Contradiction, Fact, Interaction
from app.models.base import utcnow
from app.services.context import DealerContext, build_contexts
from app.services.money import fmt

# Attributes where a changed value is a conflict rather than a legitimate update.
# A price moving is negotiation; a VIN moving is a contradiction.
IMMUTABLE_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "vehicle.is_demo",
        "vehicle.is_loaner",
        "vehicle.vin",
        "vehicle.damage_history",
        "dealer.no_mandatory_add_ons",
        "dealer.no_dealer_fees",
        "offer.financing_required",
        "offer.prepayment_penalty",
        "offer.discount_clawback",
    }
)

ATTRIBUTE_LABELS: dict[str, str] = {
    "vehicle.is_demo": "demo status",
    "vehicle.is_loaner": "loaner status",
    "vehicle.vin": "VIN",
    "vehicle.damage_history": "damage history",
    "vehicle.mileage": "mileage",
    "dealer.no_mandatory_add_ons": "whether add-ons are mandatory",
    "offer.financing_required": "whether financing is required",
}


def _source_label(db: Session, interaction_id: int | None) -> str:
    if interaction_id is None:
        return "an unrecorded source"
    interaction = db.get(Interaction, interaction_id)
    if interaction is None:
        return "an unrecorded source"
    channel = str(interaction.channel).replace("_", " ").lower()
    return f"{channel} on {interaction.occurred_at:%b %d at %H:%M}"


def _upsert(
    db: Session,
    *,
    dealer_id: int,
    dedupe_key: str,
    kind: ContradictionKind,
    summary: str,
    detail_a: str,
    detail_b: str,
    severity: Severity = Severity.WARNING,
    attribute: str | None = None,
    fact_a_id: int | None = None,
    fact_b_id: int | None = None,
    interaction_a_id: int | None = None,
    interaction_b_id: int | None = None,
) -> Contradiction:
    existing = db.scalars(
        select(Contradiction).where(Contradiction.dedupe_key == dedupe_key)
    ).first()
    if existing is not None:
        existing.summary = summary
        existing.detail_a = detail_a
        existing.detail_b = detail_b
        existing.severity = str(severity)
        db.flush()
        return existing

    row = Contradiction(
        dealer_id=dealer_id,
        attribute=attribute,
        kind=str(kind),
        severity=str(severity),
        fact_a_id=fact_a_id,
        fact_b_id=fact_b_id,
        interaction_a_id=interaction_a_id,
        interaction_b_id=interaction_b_id,
        summary=summary,
        detail_a=detail_a,
        detail_b=detail_b,
        detected_by="RULE",
        dedupe_key=dedupe_key,
        detected_at=utcnow(),
    )
    db.add(row)
    db.flush()
    return row


# ----------------------------------------------------------------- detectors


def detect_fact_conflicts(db: Session, ctx: DealerContext) -> list[Contradiction]:
    """Two dealer statements about something that should not change."""
    found: list[Contradiction] = []
    facts = db.scalars(
        select(Fact)
        .where(
            Fact.dealer_id == ctx.dealer.id,
            Fact.attribute.in_(IMMUTABLE_ATTRIBUTES),
            Fact.status.in_([FactStatus.CURRENT, FactStatus.SUPERSEDED]),
        )
        .order_by(Fact.observed_at)
    ).all()

    by_attribute: dict[str, list[Fact]] = {}
    for fact in facts:
        by_attribute.setdefault(fact.attribute, []).append(fact)

    for attribute, rows in by_attribute.items():
        for earlier, later in zip(rows, rows[1:], strict=False):
            same = (
                earlier.value_bool == later.value_bool
                and earlier.value_text == later.value_text
                and earlier.value_number == later.value_number
            )
            if same:
                continue
            label = ATTRIBUTE_LABELS.get(attribute, attribute)
            found.append(
                _upsert(
                    db,
                    dealer_id=ctx.dealer.id,
                    dedupe_key=f"fact:{earlier.id}:{later.id}",
                    kind=ContradictionKind.VALUE_CONFLICT,
                    attribute=attribute,
                    summary=f"{ctx.dealer.name} gave two different answers about {label}.",
                    detail_a=(
                        f"{_source_label(db, earlier.interaction_id)}: "
                        f"{label} = {earlier.display_value}"
                        + (f' — "{earlier.quote}"' if earlier.quote else "")
                    ),
                    detail_b=(
                        f"{_source_label(db, later.interaction_id)}: "
                        f"{label} = {later.display_value}"
                        + (f' — "{later.quote}"' if later.quote else "")
                    ),
                    fact_a_id=earlier.id,
                    fact_b_id=later.id,
                    interaction_a_id=earlier.interaction_id,
                    interaction_b_id=later.interaction_id,
                    severity=Severity.WARNING,
                )
            )
    return found


def detect_add_on_denial(db: Session, ctx: DealerContext) -> list[Contradiction]:
    """"No mandatory add-ons" followed by a quote carrying add-ons.

    This is the canonical case from the brief: a phone call at 2:14 PM and a written
    quote at 4:03 PM that disagree.
    """
    denials = db.scalars(
        select(Fact).where(
            Fact.dealer_id == ctx.dealer.id,
            Fact.attribute == "dealer.no_mandatory_add_ons",
            Fact.value_bool.is_(True),
            Fact.status.in_([FactStatus.CURRENT, FactStatus.SUPERSEDED]),
        )
    ).all()
    if not denials:
        return []

    found: list[Contradiction] = []
    # Only the offer that currently stands. Re-flagging every historical version that
    # carried the same add-ons is noise, not new information.
    standing = ctx.current_pricing
    for result in ([standing] if standing is not None else []):
        add_ons = [
            line for line in result.lines if line.kind == "ADD_ON" and line.price_cents > 0
        ]
        if not add_ons:
            continue
        total = sum(line.price_cents for line in add_ons)
        offer = next((o for o in ctx.offers if o.id == result.offer_id), None)
        for denial in denials:
            # Only a denial that came *before* the quote is a contradiction; a denial
            # afterwards is the dealer agreeing to remove them.
            if denial.observed_at and offer and denial.observed_at > offer.quoted_at:
                continue
            found.append(
                _upsert(
                    db,
                    dealer_id=ctx.dealer.id,
                    dedupe_key=f"addon_denial:{denial.id}:{result.offer_id}",
                    kind=ContradictionKind.PRESENCE_CONFLICT,
                    attribute="dealer.no_mandatory_add_ons",
                    summary=(
                        f"{ctx.dealer.name} said there were no mandatory add-ons, then "
                        f"quoted {fmt(total)} of them."
                    ),
                    detail_a=(
                        f"{_source_label(db, denial.interaction_id)}: dealer stated there "
                        f"are no mandatory add-ons"
                        + (f' — "{denial.quote}"' if denial.quote else "")
                    ),
                    detail_b=(
                        f"{_source_label(db, offer.interaction_id) if offer else 'quote'}: "
                        f"offer v{result.version} includes "
                        + ", ".join(f"{line.name} {fmt(line.price_cents)}" for line in add_ons)
                        + f" ({fmt(total)} total)"
                    ),
                    fact_a_id=denial.id,
                    interaction_a_id=denial.interaction_id,
                    interaction_b_id=offer.interaction_id if offer else None,
                    severity=Severity.CRITICAL,
                )
            )
    return found


def detect_price_regression(db: Session, ctx: DealerContext) -> list[Contradiction]:
    """A later offer that costs more than an earlier one, on the dealer's own terms."""
    history = ctx.offer_history
    found: list[Contradiction] = []
    for earlier, later in zip(history, history[1:], strict=False):
        if (
            earlier.dealer_controlled_cents is None
            or later.dealer_controlled_cents is None
            or later.dealer_controlled_cents <= earlier.dealer_controlled_cents
        ):
            continue
        delta = later.dealer_controlled_cents - earlier.dealer_controlled_cents
        found.append(
            _upsert(
                db,
                dealer_id=ctx.dealer.id,
                dedupe_key=f"regression:{earlier.offer_id}:{later.offer_id}",
                kind=ContradictionKind.NUMERIC_CONFLICT,
                attribute="offer.dealer_controlled",
                summary=(
                    f"{ctx.dealer.name}'s price moved up by {fmt(delta)} between "
                    f"v{earlier.version} and v{later.version}."
                ),
                detail_a=(
                    f"v{earlier.version} on {earlier.quoted_at:%b %d}: "
                    f"{fmt(earlier.dealer_controlled_cents)} dealer-controlled"
                ),
                detail_b=(
                    f"v{later.version} on {later.quoted_at:%b %d}: "
                    f"{fmt(later.dealer_controlled_cents)} dealer-controlled"
                ),
                severity=Severity.WARNING,
            )
        )
    return found


def detect_broken_commitments(db: Session, ctx: DealerContext) -> list[Contradiction]:
    """A promise with a due date that passed with nothing delivered."""
    found: list[Contradiction] = []
    for commitment in ctx.commitments:
        if commitment.status != CommitmentStatus.OPEN or commitment.due_at is None:
            continue
        if commitment.due_at >= ctx.now:
            continue
        delivered = any(
            i.occurred_at > commitment.due_at
            for i in ctx.inbound
            if commitment.party == "DEALER"
        )
        if delivered:
            continue
        overdue_days = (ctx.now - commitment.due_at).total_seconds() / 86400
        found.append(
            _upsert(
                db,
                dealer_id=ctx.dealer.id,
                dedupe_key=f"commitment:{commitment.id}",
                kind=ContradictionKind.PROMISE_BROKEN,
                summary=(
                    f"{ctx.dealer.name} promised something that is now "
                    f"{overdue_days:.0f} day(s) overdue."
                ),
                detail_a=(
                    f"{_source_label(db, commitment.interaction_id)}: "
                    f'"{commitment.text}" — due {commitment.due_at:%b %d at %H:%M}'
                ),
                detail_b="Nothing has arrived since the deadline passed.",
                interaction_a_id=commitment.interaction_id,
                severity=Severity.WARNING,
            )
        )
    return found


DETECTORS = (
    detect_fact_conflicts,
    detect_add_on_denial,
    detect_price_regression,
    detect_broken_commitments,
)


def detect_for_dealer(db: Session, ctx: DealerContext) -> list[Contradiction]:
    found: list[Contradiction] = []
    for detector in DETECTORS:
        found.extend(detector(db, ctx))
    return found


def detect_all(db: Session) -> list[Contradiction]:
    found: list[Contradiction] = []
    for ctx in build_contexts(db).values():
        found.extend(detect_for_dealer(db, ctx))
    return found
