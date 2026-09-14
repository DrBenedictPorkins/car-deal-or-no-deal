"""The canned questions from the brief, answered with exact SQL.

None of these calls a model. The point of the structured data model is that these
questions have precise answers, and a precise answer beats a plausible one.

Phase 5 adds natural-language input on top: the model picks *which* of these to run
(and with what parameters) and application code executes it — the model never invents
the answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import FactStatus, FeeKind
from app.models import Fact, Offer, OfferLine
from app.services.context import build_contexts
from app.services.money import fmt


@dataclass
class Answer:
    question: str
    answer: str
    rows: list[dict[str, Any]] = field(default_factory=list)


def best_offer(db: Session) -> Answer:
    contexts = [
        c
        for c in build_contexts(db).values()
        if c.current_pricing and c.current_pricing.effective_otd_cents is not None
    ]
    if not contexts:
        return Answer("What is our best offer?", "No dealer has given a priced offer yet.")
    ranked = sorted(contexts, key=lambda c: c.current_pricing.effective_otd_cents)
    best = ranked[0]
    p = best.current_pricing
    return Answer(
        "What is our best offer?",
        f"{best.dealer.name} at {fmt(p.effective_otd_cents)} out the door "
        f"({fmt(p.dealer_controlled_cents)} dealer-controlled).",
        [
            {
                "dealer": c.dealer.name,
                "otd_cents": c.current_pricing.effective_otd_cents,
                "dealer_controlled_cents": c.current_pricing.dealer_controlled_cents,
                "written_otd": c.has_written_otd,
            }
            for c in ranked
        ],
    )


def no_response(db: Session) -> Answer:
    contexts = [c for c in build_contexts(db).values() if c.outbound and not c.inbound]
    return Answer(
        "Who hasn't responded?",
        ", ".join(c.dealer.name for c in contexts) or "Everyone contacted has replied.",
        [
            {
                "dealer": c.dealer.name,
                "messages_sent": len(c.outbound),
                "days_since_contact": round(c.idle_days or 0, 1),
            }
            for c in contexts
        ],
    )


def owes_me(db: Session) -> Answer:
    rows = []
    for c in build_contexts(db).values():
        party, reason = c.owes_response()
        if party == "DEALER":
            rows.append(
                {
                    "dealer": c.dealer.name,
                    "reason": reason,
                    "idle_hours": round(c.idle_hours or 0, 1),
                }
            )
    rows.sort(key=lambda r: -r["idle_hours"])
    return Answer(
        "Who owes me a response?",
        ", ".join(r["dealer"] for r in rows) or "Nobody — the ball is in your court.",
        rows,
    )


def phone_pressure(db: Session) -> Answer:
    rows = []
    for c in build_contexts(db).values():
        codes = {
            s.code
            for s in c.signals
            if s.code in ("INSISTS_ON_PHONE_CALL", "REPEATED_PHONE_REQUEST", "INSISTS_ON_VISIT")
        }
        if codes:
            rows.append({"dealer": c.dealer.name, "signals": sorted(codes)})
    return Answer(
        "Which dealerships are trying to get me on the phone or into the showroom?",
        ", ".join(r["dealer"] for r in rows) or "None detected.",
        rows,
    )


def written_otd(db: Session) -> Answer:
    rows = []
    for c in build_contexts(db).values():
        p = c.current_pricing
        if p is None or p.quoted_otd_cents is None:
            continue
        rows.append(
            {
                "dealer": c.dealer.name,
                "otd_cents": p.quoted_otd_cents,
                "reconciled": p.otd_reconciled,
                "variance_cents": p.otd_variance_cents,
            }
        )
    rows.sort(key=lambda r: r["otd_cents"])
    return Answer(
        "Who gave us an actual written OTD?",
        ", ".join(r["dealer"] for r in rows) or "Nobody has put an OTD in writing.",
        rows,
    )


def lowest_selling_price(db: Session) -> Answer:
    rows = [
        {
            "dealer": c.dealer.name,
            "selling_price_cents": c.current_pricing.selling_price_cents,
        }
        for c in build_contexts(db).values()
        if c.current_pricing and c.current_pricing.selling_price_cents is not None
    ]
    rows.sort(key=lambda r: r["selling_price_cents"])
    return Answer(
        "Which dealer has the lowest selling price?",
        f"{rows[0]['dealer']} at {fmt(rows[0]['selling_price_cents'])}"
        if rows
        else "No selling prices on record.",
        rows,
    )


def first_quote(db: Session, dealer_id: int) -> Answer:
    offer = db.scalars(
        select(Offer).where(Offer.dealer_id == dealer_id).order_by(Offer.version).limit(1)
    ).first()
    if offer is None:
        return Answer("What did they originally quote?", "No offers on record.")
    from app.services import pricing

    result = pricing.compute(offer)
    return Answer(
        "What did they originally quote?",
        f"Version 1 on {offer.quoted_at:%b %d}: {fmt(result.effective_otd_cents)} OTD, "
        f"{fmt(result.selling_price_cents)} selling price.",
        [
            {
                "version": result.version,
                "quoted_at": offer.quoted_at.isoformat(),
                "selling_price_cents": result.selling_price_cents,
                "otd_cents": result.effective_otd_cents,
            }
        ],
    )


def demo_confirmations(db: Session) -> Answer:
    facts = db.scalars(
        select(Fact).where(
            Fact.attribute == "vehicle.is_demo", Fact.status == FactStatus.CURRENT
        )
    ).all()
    confirmed = [f for f in facts if f.value_bool is False]
    rows = [
        {
            "dealer_id": f.dealer_id,
            "value": f.display_value,
            "quote": f.quote,
            "interaction_id": f.interaction_id,
            "fact_id": f.id,
        }
        for f in facts
    ]
    return Answer(
        "Did anyone confirm the car wasn't a demo?",
        f"{len(confirmed)} dealer(s) affirmatively said the car is not a demo."
        if confirmed
        else "Nobody has confirmed demo status either way.",
        rows,
    )


def add_on_by_name(db: Session, needle: str) -> Answer:
    rows = []
    for line, offer in db.execute(
        select(OfferLine, Offer)
        .join(Offer, OfferLine.offer_id == Offer.id)
        .where(OfferLine.kind == FeeKind.ADD_ON, OfferLine.name.ilike(f"%{needle}%"))
    ).all():
        rows.append(
            {
                "dealer_id": offer.dealer_id,
                "offer_version": offer.version,
                "name": line.name,
                "price_cents": line.price_cents,
                "mandatory_claimed": line.mandatory_claimed,
            }
        )
    return Answer(
        f"Who added {needle}?",
        f"{len(rows)} quote line(s) match." if rows else f"No quote includes {needle}.",
        rows,
    )


def changed_since(db: Session, hours: int = 24) -> Answer:
    from app.services.dashboard import recent_changes

    lines = recent_changes(db, since_hours=hours, limit=50)
    return Answer(
        f"What changed in the last {hours} hours?",
        f"{len(lines)} change(s)." if lines else "Nothing changed.",
        [{"change": line} for line in lines],
    )


CANNED = {
    "best_offer": best_offer,
    "no_response": no_response,
    "owes_me": owes_me,
    "phone_pressure": phone_pressure,
    "written_otd": written_otd,
    "lowest_selling_price": lowest_selling_price,
    "demo_confirmations": demo_confirmations,
}
