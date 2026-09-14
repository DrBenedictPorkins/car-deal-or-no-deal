from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import DbSession, get_dealer_or_404
from app.models import Offer
from app.schemas.entities import OfferIn, OfferOut, PricingOut
from app.services import offers as offer_service
from app.services import pricing, state_engine
from app.services.context import get_profile

router = APIRouter(prefix="/api/offers", tags=["offers"])


def _serialize(db: Session, offer: Offer) -> OfferOut:
    profile = get_profile(db)
    result = pricing.compute(
        offer, expected_tax_rate_bp=profile.expected_tax_rate_bp if profile else None
    )
    out = OfferOut.model_validate(offer)
    out.pricing = PricingOut(
        offer_id=result.offer_id,
        version=result.version,
        quoted_at=result.quoted_at,
        msrp_cents=result.msrp_cents,
        selling_price_cents=result.selling_price_cents,
        price_basis=result.price_basis,
        add_ons_total_cents=result.add_ons_total_cents,
        dealer_fees_total_cents=result.dealer_fees_total_cents,
        government_total_cents=result.government_total_cents,
        dealer_controlled_cents=result.dealer_controlled_cents,
        computed_otd_cents=result.computed_otd_cents,
        quoted_otd_cents=result.quoted_otd_cents,
        effective_otd_cents=result.effective_otd_cents,
        otd_variance_cents=result.otd_variance_cents,
        otd_reconciled=result.otd_reconciled,
        discount_from_msrp_cents=result.discount_from_msrp_cents,
        taxable_base_cents=result.taxable_base_cents,
        implied_tax_rate_bp=result.implied_tax_rate_bp,
        expected_tax_rate_bp=result.expected_tax_rate_bp,
        tax_rate_variance_bp=result.tax_rate_variance_bp,
        unwanted_add_ons_cents=result.unwanted_add_ons_cents,
        clean_dealer_controlled_cents=result.clean_dealer_controlled_cents,
        clean_otd_cents=result.clean_otd_cents,
        fees_disclosed=result.fees_disclosed,
        government_disclosed=result.government_disclosed,
        warnings=result.warnings,
        is_complete=result.is_complete,
    )
    return out


@router.get("", response_model=list[OfferOut])
def list_offers(db: DbSession, dealer_id: int | None = None, current_only: bool = False):
    q = select(Offer)
    if dealer_id is not None:
        q = q.where(Offer.dealer_id == dealer_id)
    if current_only:
        q = q.where(Offer.is_current.is_(True))
    rows = db.scalars(q.order_by(Offer.dealer_id, Offer.version)).all()
    return [_serialize(db, offer) for offer in rows]


@router.post("", response_model=OfferOut, status_code=201)
def create_offer(payload: OfferIn, db: DbSession):
    """Record a quote as a new version. Existing versions are never modified."""
    get_dealer_or_404(db, payload.dealer_id)
    data = payload.model_dump()
    lines = data.pop("lines", [])

    # Default each add-on's user_wants from the buyer profile rather than assuming.
    profile = get_profile(db)
    if profile is not None and not profile.wants_add_ons:
        for line in lines:
            if line.get("kind") == "ADD_ON":
                line.setdefault("user_wants", False)

    offer = offer_service.create(db, data, lines)
    state_engine.refresh_one(db, offer.dealer_id)
    db.refresh(offer)
    return _serialize(db, offer)


@router.get("/{offer_id}", response_model=OfferOut)
def read_offer(offer_id: int, db: DbSession):
    offer = db.get(Offer, offer_id)
    if offer is None:
        raise HTTPException(status_code=404, detail="offer not found")
    return _serialize(db, offer)


@router.delete("/{offer_id}", status_code=204)
def delete_offer(offer_id: int, db: DbSession) -> None:
    """Removing a mis-entered quote. The predecessor is promoted back to current."""
    offer = db.get(Offer, offer_id)
    if offer is None:
        raise HTTPException(status_code=404, detail="offer not found")
    dealer_id = offer.dealer_id
    was_current = offer.is_current
    db.delete(offer)
    db.flush()
    if was_current:
        previous = db.scalars(
            select(Offer)
            .where(Offer.dealer_id == dealer_id)
            .order_by(Offer.version.desc())
            .limit(1)
        ).first()
        if previous is not None:
            previous.is_current = True
    db.flush()
    state_engine.refresh_one(db, dealer_id)
