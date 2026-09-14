"""Turning an extracted candidate into persisted structure.

The candidate is quarantined data: it came out of a parser (and later, a model), so
nothing here trusts it as final. This module is where a candidate becomes an Offer, a
set of Facts with provenance, and — when reconciliation fails or confidence is low — a
review flag rather than a silent commit.

Arithmetic still does not happen here. ``pricing.compute`` is called only to *check*
the result; no total is written to a column.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enrichment.extract_rules import OfferCandidate
from app.enums import (
    Confidence,
    ExtractionMethod,
    FeeKind,
    Party,
    SubjectType,
    VehicleCondition,
)
from app.models import BuyerProfile, Dealer, Interaction, Offer, Vehicle
from app.services import facts as fact_service
from app.services import offers as offer_service
from app.services import pricing

# Fields the extractor can set that map straight onto Offer columns.
_DIRECT_FIELDS = (
    "msrp_cents",
    "advertised_price_cents",
    "selling_price_cents",
    "discount_cents",
    "destination_cents",
    "doc_fee_cents",
    "processing_fee_cents",
    "other_taxable_fees_cents",
    "other_non_tax_fees_cents",
    "tax_cents",
    "registration_cents",
    "title_fee_cents",
    "quoted_otd_cents",
)


@dataclass
class CommitResult:
    offer: Offer | None = None
    vehicle: Vehicle | None = None
    facts_written: int = 0
    skipped_reason: str | None = None
    review_reasons: list[str] = field(default_factory=list)

    @property
    def committed(self) -> bool:
        return self.offer is not None


def _find_or_create_vehicle(
    db: Session, dealer: Dealer, candidate: OfferCandidate, profile: BuyerProfile | None
) -> Vehicle:
    if candidate.vin:
        existing = db.scalars(
            select(Vehicle).where(Vehicle.dealer_id == dealer.id, Vehicle.vin == candidate.vin)
        ).first()
        if existing is not None:
            return existing
        # A dealer who has been quoting an unidentified car and now names a VIN is
        # identifying the same car, not stocking a second one.
        unidentified = db.scalars(
            select(Vehicle).where(Vehicle.dealer_id == dealer.id, Vehicle.vin.is_(None))
        ).first()
        if unidentified is not None:
            unidentified.vin = candidate.vin
            db.flush()
            return unidentified

    existing = db.scalars(select(Vehicle).where(Vehicle.dealer_id == dealer.id)).first()
    if existing is not None:
        return existing

    vehicle = Vehicle(
        dealer_id=dealer.id,
        year=profile.target_year if profile else None,
        make=profile.target_make if profile else None,
        model=profile.target_model if profile else None,
        trim=profile.target_trim if profile else None,
        vin=candidate.vin,
        condition=VehicleCondition.NEW,
    )
    db.add(vehicle)
    db.flush()
    return vehicle


def _record_vehicle_facts(
    db: Session,
    dealer: Dealer,
    vehicle: Vehicle,
    candidate: OfferCandidate,
    interaction: Interaction,
) -> int:
    written = 0
    common = {
        "dealer_id": dealer.id,
        "interaction_id": interaction.id,
        "method": ExtractionMethod.RULE,
        "asserted_by_party": Party.DEALER,
        "observed_at": interaction.occurred_at,
        "subject_type": SubjectType.VEHICLE,
        "subject_id": vehicle.id,
    }
    if candidate.vin:
        fact_service.record(
            db, attribute="vehicle.vin", value_text=candidate.vin,
            quote=candidate.vin, confidence=0.95, **common
        )
        vehicle.vin = candidate.vin
        written += 1
    if candidate.mileage is not None:
        fact_service.record(
            db, attribute="vehicle.mileage", value_number=candidate.mileage,
            value_unit="miles", quote=f"{candidate.mileage} miles", confidence=0.9, **common
        )
        vehicle.mileage = candidate.mileage
        written += 1
    if candidate.is_demo is not None:
        fact_service.record(
            db, attribute="vehicle.is_demo", value_bool=candidate.is_demo,
            quote="not a demo" if candidate.is_demo is False else "demo", confidence=0.85,
            **common,
        )
        vehicle.is_demo = candidate.is_demo
        written += 1
    if candidate.is_loaner is not None:
        fact_service.record(
            db, attribute="vehicle.is_loaner", value_bool=candidate.is_loaner,
            quote="not a loaner", confidence=0.85, **common
        )
        vehicle.is_loaner = candidate.is_loaner
        written += 1
    if "msrp_cents" in candidate.fields and vehicle.msrp_cents is None:
        vehicle.msrp_cents = candidate.fields["msrp_cents"]
    db.flush()
    return written


def commit(
    db: Session,
    *,
    dealer: Dealer,
    candidate: OfferCandidate,
    interaction: Interaction,
    profile: BuyerProfile | None = None,
) -> CommitResult:
    """Persist a candidate as an Offer with provenance, or explain why not."""
    if not candidate.has_pricing:
        return CommitResult(skipped_reason="No pricing in this message.")

    # Re-ingesting the same message must not create a second version of its offer.
    existing = db.scalars(
        select(Offer).where(Offer.interaction_id == interaction.id)
    ).first()
    if existing is not None:
        return CommitResult(offer=existing, skipped_reason="Already extracted.")

    vehicle = _find_or_create_vehicle(db, dealer, candidate, profile)

    payload: dict = {
        "dealer_id": dealer.id,
        "vehicle_id": vehicle.id,
        "interaction_id": interaction.id,
        "campaign_id": vehicle.campaign_id,
        "quoted_at": interaction.occurred_at,
        "confidence": candidate.confidence,
        "advertised_includes_fees": candidate.advertised_includes_fees,
        "financing_required": candidate.financing_required,
        "financing_provider": candidate.financing_provider,
        "apr_bp": candidate.apr_bp,
        "financing_term_months": candidate.financing_term_months,
        "minimum_loan_months": candidate.minimum_loan_months,
        "prepayment_penalty": candidate.prepayment_penalty,
        "discount_clawback": candidate.discount_clawback,
        "deadline_note": candidate.deadline_note,
        "notes": " ".join(candidate.notes) or None,
    }
    for name in _DIRECT_FIELDS:
        payload[name] = candidate.fields.get(name)

    wants_add_ons = bool(profile.wants_add_ons) if profile else False
    lines = [
        {
            "kind": FeeKind.ADD_ON,
            "name": add_on.name,
            "price_cents": add_on.price_cents,
            "is_taxable": True,
            "mandatory_claimed": add_on.mandatory_claimed,
            "already_installed": add_on.already_installed,
            "user_wants": wants_add_ons,
            "notes": add_on.quote,
        }
        for add_on in candidate.add_ons
    ]

    offer = offer_service.create(db, payload, lines)

    # Reconciliation is a check on the extraction, not a correction to it.
    result = pricing.compute(
        offer, expected_tax_rate_bp=profile.expected_tax_rate_bp if profile else None
    )
    review_reasons: list[str] = list(candidate.conflicts)
    if candidate.confidence == Confidence.LOW:
        review_reasons.append("Extraction confidence is low.")
    if result.otd_variance_cents:
        review_reasons.append(
            f"Quoted OTD differs from the line items by "
            f"{abs(result.otd_variance_cents) / 100:,.2f} dollars."
        )
    offer.needs_review = bool(review_reasons)
    db.flush()

    written = _record_vehicle_facts(db, dealer, vehicle, candidate, interaction)
    for evidence in candidate.evidence:
        fact_service.record(
            db,
            subject_type=SubjectType.OFFER,
            subject_id=offer.id,
            attribute=f"offer.{evidence.target.removesuffix('_cents')}",
            dealer_id=dealer.id,
            value_number=evidence.amount_cents,
            value_unit="cents",
            interaction_id=interaction.id,
            offer_id=offer.id,
            quote=evidence.quote,
            quote_start=evidence.start,
            quote_end=evidence.end,
            method=ExtractionMethod.RULE,
            asserted_by_party=Party.DEALER,
            observed_at=interaction.occurred_at,
        )
        written += 1

    if candidate.financing_required is not None:
        fact_service.record(
            db,
            subject_type=SubjectType.OFFER,
            subject_id=offer.id,
            attribute="offer.financing_required",
            dealer_id=dealer.id,
            value_bool=candidate.financing_required,
            interaction_id=interaction.id,
            offer_id=offer.id,
            quote=candidate.financing_provider or "financing condition stated",
            method=ExtractionMethod.RULE,
            asserted_by_party=Party.DEALER,
            observed_at=interaction.occurred_at,
        )
        written += 1

    return CommitResult(
        offer=offer, vehicle=vehicle, facts_written=written, review_reasons=review_reasons
    )
