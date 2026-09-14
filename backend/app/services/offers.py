"""Offer creation and versioning.

Offers are never edited in place. Recording a new quote from the same dealer creates
version N+1, points it at its predecessor, and demotes the predecessor — so the
progression from "$31,306.22 OTD" to "$30,500.01 OTD" stays visible forever.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Offer, OfferLine


def _next_version(db: Session, dealer_id: int) -> tuple[int, Offer | None]:
    latest = db.scalars(
        select(Offer)
        .where(Offer.dealer_id == dealer_id)
        .order_by(Offer.version.desc(), Offer.id.desc())
        .limit(1)
    ).first()
    return ((latest.version + 1) if latest else 1), latest


def create(db: Session, payload: dict, lines: list[dict]) -> Offer:
    """Create the next version of a dealer's offer.

    The single-current invariant is enforced here rather than by a trigger, because
    "current" is per dealer and SQLite partial indexes cannot express the demotion.
    """
    dealer_id = payload["dealer_id"]
    version, previous = _next_version(db, dealer_id)

    for stale in db.scalars(
        select(Offer).where(Offer.dealer_id == dealer_id, Offer.is_current.is_(True))
    ).all():
        stale.is_current = False

    offer = Offer(
        **payload,
        version=version,
        supersedes_offer_id=previous.id if previous else None,
        is_current=True,
    )
    db.add(offer)
    db.flush()

    for line in lines:
        db.add(OfferLine(offer_id=offer.id, **line))
    db.flush()
    db.refresh(offer)
    return offer


def history(db: Session, dealer_id: int) -> list[Offer]:
    return list(
        db.scalars(
            select(Offer).where(Offer.dealer_id == dealer_id).order_by(Offer.version)
        ).all()
    )


def current(db: Session, dealer_id: int) -> Offer | None:
    return db.scalars(
        select(Offer)
        .where(Offer.dealer_id == dealer_id, Offer.is_current.is_(True))
        .order_by(Offer.version.desc())
        .limit(1)
    ).first()
