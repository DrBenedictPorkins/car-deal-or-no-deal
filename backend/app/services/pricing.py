"""Deterministic offer arithmetic.

Everything in this module is pure application code. No LLM ever computes any of it,
and no value here is read back from storage as "truth" — it is recomputed on every
read from the offer's line items.

Normalization rule (ARCHITECTURE.md §5):

    DEALER-CONTROLLED = selling_price + dealer fees + add-ons
    OTD               = dealer-controlled + government charges

Government charges (tax, registration, title) are excluded from the dealer-controlled
number because they vary by the buyer's jurisdiction and the dealer cannot move them.
Including them lets a low-tax dealer look cheaper while offering a worse car deal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.enums import FeeKind
from app.models.offer import Offer, OfferLine
from app.services.money import fmt


def _sum(*values: int | None) -> int:
    return sum(v for v in values if v is not None)


@dataclass(frozen=True)
class LineBreakdown:
    id: int
    kind: str
    name: str
    price_cents: int
    is_dealer_controlled: bool
    is_taxable: bool | None
    mandatory_claimed: bool | None
    already_installed: bool | None
    user_wants: bool
    removable_confirmed: bool | None
    notes: str | None


@dataclass
class PricingResult:
    """Everything derived from one offer. Recomputed, never stored as truth."""

    offer_id: int
    version: int
    quoted_at: datetime

    msrp_cents: int | None
    selling_price_cents: int | None
    # Which figure the dealer-controlled cost was built from: a quoted selling price,
    # or an advertised price standing in for one.
    price_basis: str | None

    add_ons_total_cents: int
    dealer_fees_total_cents: int
    government_total_cents: int

    dealer_controlled_cents: int | None
    computed_otd_cents: int | None
    quoted_otd_cents: int | None
    effective_otd_cents: int | None

    # quoted − computed. Non-zero means a charge that isn't in the line items.
    otd_variance_cents: int | None
    otd_reconciled: bool

    discount_from_msrp_cents: int | None

    taxable_base_cents: int | None
    implied_tax_rate_bp: int | None
    expected_tax_rate_bp: int | None
    tax_rate_variance_bp: int | None

    # What this offer would cost with unwanted, removable add-ons stripped out.
    unwanted_add_ons_cents: int
    clean_dealer_controlled_cents: int | None
    clean_otd_cents: int | None

    # False when the dealer named no fees at all. Their absence is not evidence of
    # zero, so the dealer-controlled cost is a floor rather than a figure.
    fees_disclosed: bool = True
    government_disclosed: bool = True

    lines: list[LineBreakdown] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    is_complete: bool = False

    @property
    def has_written_otd(self) -> bool:
        return self.quoted_otd_cents is not None


def _lines_of(offer: Offer, kind: FeeKind) -> list[OfferLine]:
    return [line for line in offer.lines if line.kind == kind]


def compute(offer: Offer, *, expected_tax_rate_bp: int | None = None) -> PricingResult:
    """Derive every headline number for a single offer."""
    add_on_lines = _lines_of(offer, FeeKind.ADD_ON)
    add_ons_total = sum(line.price_cents for line in add_on_lines)

    # Dealer fees: the named columns plus any itemized DEALER_FEE line. A line
    # flagged is_dealer_controlled=False (assumption A1 overridden by the user)
    # moves to the government side instead.
    extra_dealer_fee_lines = [
        line for line in _lines_of(offer, FeeKind.DEALER_FEE) if line.is_dealer_controlled
    ]
    dealer_fees_total = _sum(
        offer.doc_fee_cents,
        offer.processing_fee_cents,
        offer.other_taxable_fees_cents,
    ) + sum(line.price_cents for line in extra_dealer_fee_lines)

    government_lines = [
        line
        for line in offer.lines
        if line.kind == FeeKind.GOVERNMENT
        or (line.kind == FeeKind.DEALER_FEE and not line.is_dealer_controlled)
    ]
    government_total = _sum(
        offer.tax_cents,
        offer.registration_cents,
        offer.title_fee_cents,
        offer.other_non_tax_fees_cents,
    ) + sum(line.price_cents for line in government_lines)

    selling = offer.selling_price_cents
    if selling is not None:
        price_basis = "SELLING"
        dealer_controlled = selling + dealer_fees_total + add_ons_total
    elif offer.advertised_price_cents is not None:
        # An advertised price is a weaker basis than a quote, but it is a real
        # dealer-controlled figure and dropping it would hide the dealer entirely
        # from the comparison.
        price_basis = "ADVERTISED"
        dealer_controlled = offer.advertised_price_cents + add_ons_total
        if not offer.advertised_includes_fees:
            dealer_controlled += dealer_fees_total
    else:
        price_basis = None
        dealer_controlled = None
    # An OTD is only derivable when the government side is actually known. Adding up
    # a selling price and a doc fee and calling it "out the door" would make a dealer
    # who simply withheld the tax line look like the cheapest in the comparison.
    has_government_data = offer.tax_cents is not None
    computed_otd = (
        dealer_controlled + government_total
        if dealer_controlled is not None and has_government_data
        else None
    )

    quoted_otd = offer.quoted_otd_cents
    # The quoted number is what the buyer would actually pay, so it wins for ranking;
    # the variance is surfaced rather than silently reconciled away.
    effective_otd = quoted_otd if quoted_otd is not None else computed_otd

    variance = (
        quoted_otd - computed_otd
        if quoted_otd is not None and computed_otd is not None
        else None
    )

    basis_amount = selling if selling is not None else offer.advertised_price_cents
    discount = (
        offer.msrp_cents - basis_amount
        if offer.msrp_cents is not None and basis_amount is not None
        else None
    )

    # Taxable base: selling price plus the fees and add-ons the dealer charges tax on.
    # Doc and processing fees are taxable in the common case — Stamford's quote only
    # reconciles to exactly 6.00% PA tax when they are included — so they are in the
    # base by default. A fee that a given state does not tax should be recorded as an
    # ``other_non_tax_fees`` amount or a GOVERNMENT line instead.
    taxable_base: int | None = None
    implied_bp: int | None = None
    tax_variance_bp: int | None = None
    if basis_amount is not None and offer.tax_cents:
        taxable_extras = _sum(
            offer.doc_fee_cents,
            offer.processing_fee_cents,
            offer.other_taxable_fees_cents,
        ) + sum(line.price_cents for line in offer.lines if line.is_taxable)
        taxable_base = basis_amount + taxable_extras
        if taxable_base > 0:
            implied_bp = round(offer.tax_cents * 10_000 / taxable_base)
            if expected_tax_rate_bp is not None:
                tax_variance_bp = implied_bp - expected_tax_rate_bp

    unwanted = sum(
        line.price_cents
        for line in add_on_lines
        if not line.user_wants and line.removable_confirmed is not False
    )
    clean_dealer_controlled = (
        dealer_controlled - unwanted if dealer_controlled is not None else None
    )
    clean_otd = (
        effective_otd - unwanted if effective_otd is not None else None
    )

    fees_disclosed = any(
        value is not None
        for value in (
            offer.doc_fee_cents,
            offer.processing_fee_cents,
            offer.other_taxable_fees_cents,
        )
    ) or any(line.kind == FeeKind.DEALER_FEE for line in offer.lines)

    warnings: list[str] = []
    if price_basis == "ADVERTISED":
        warnings.append(
            "No selling price was quoted — this uses the advertised price"
            + (
                ", which the dealer says already includes their fees."
                if offer.advertised_includes_fees
                else ", which may not be what they would actually write up."
            )
        )
    if basis_amount is not None and not fees_disclosed:
        warnings.append(
            "No dealer fees disclosed. Nearly every dealer charges one, so the "
            "dealer-controlled cost here is a floor, not a final number."
        )
    if variance:
        warnings.append(
            f"Quoted OTD {fmt(quoted_otd)} does not match the line items "
            f"({fmt(computed_otd)}) — {fmt(abs(variance))} is "
            f"{'unexplained' if variance > 0 else 'unaccounted for'}."
        )
    if tax_variance_bp is not None and abs(tax_variance_bp) >= 10:
        warnings.append(
            f"Implied tax rate is {implied_bp / 100:.2f}% but the profile expects "
            f"{expected_tax_rate_bp / 100:.2f}% — the tax base may include an "
            f"undisclosed charge."
        )
    if basis_amount is None:
        warnings.append("No selling price on this offer.")
    if quoted_otd is None and computed_otd is None:
        warnings.append(
            "No out-the-door number: the dealer has not quoted one and has not given "
            "enough line items to derive one."
        )

    is_complete = (
        price_basis == "SELLING"
        and offer.tax_cents is not None
        and effective_otd is not None
    )

    return PricingResult(
        offer_id=offer.id,
        version=offer.version,
        quoted_at=offer.quoted_at,
        msrp_cents=offer.msrp_cents,
        selling_price_cents=selling,
        price_basis=price_basis,
        add_ons_total_cents=add_ons_total,
        dealer_fees_total_cents=dealer_fees_total,
        government_total_cents=government_total,
        dealer_controlled_cents=dealer_controlled,
        computed_otd_cents=computed_otd,
        quoted_otd_cents=quoted_otd,
        effective_otd_cents=effective_otd,
        otd_variance_cents=variance,
        otd_reconciled=variance == 0 if variance is not None else quoted_otd is None,
        discount_from_msrp_cents=discount,
        taxable_base_cents=taxable_base,
        implied_tax_rate_bp=implied_bp,
        expected_tax_rate_bp=expected_tax_rate_bp,
        tax_rate_variance_bp=tax_variance_bp,
        unwanted_add_ons_cents=unwanted,
        clean_dealer_controlled_cents=clean_dealer_controlled,
        clean_otd_cents=clean_otd,
        fees_disclosed=fees_disclosed,
        government_disclosed=has_government_data,
        lines=[
            LineBreakdown(
                id=line.id,
                kind=line.kind,
                name=line.name,
                price_cents=line.price_cents,
                is_dealer_controlled=line.is_dealer_controlled,
                is_taxable=line.is_taxable,
                mandatory_claimed=line.mandatory_claimed,
                already_installed=line.already_installed,
                user_wants=line.user_wants,
                removable_confirmed=line.removable_confirmed,
                notes=line.notes,
            )
            for line in offer.lines
        ],
        warnings=warnings,
        is_complete=is_complete,
    )


@dataclass(frozen=True)
class OfferDelta:
    """Movement between two versions of the same dealer's offer."""

    from_version: int
    to_version: int
    selling_price_delta_cents: int | None
    dealer_controlled_delta_cents: int | None
    otd_delta_cents: int | None
    add_ons_delta_cents: int
    added_line_names: tuple[str, ...]
    removed_line_names: tuple[str, ...]

    @property
    def improved(self) -> bool:
        return (self.otd_delta_cents or 0) < 0


def diff(previous: PricingResult, current: PricingResult) -> OfferDelta:
    def sub(a: int | None, b: int | None) -> int | None:
        return a - b if a is not None and b is not None else None

    prev_names = {line.name.strip().lower() for line in previous.lines}
    curr_names = {line.name.strip().lower() for line in current.lines}
    name_by_key = {line.name.strip().lower(): line.name for line in current.lines + previous.lines}

    return OfferDelta(
        from_version=previous.version,
        to_version=current.version,
        selling_price_delta_cents=sub(current.selling_price_cents, previous.selling_price_cents),
        dealer_controlled_delta_cents=sub(
            current.dealer_controlled_cents, previous.dealer_controlled_cents
        ),
        otd_delta_cents=sub(current.effective_otd_cents, previous.effective_otd_cents),
        add_ons_delta_cents=current.add_ons_total_cents - previous.add_ons_total_cents,
        added_line_names=tuple(name_by_key[n] for n in sorted(curr_names - prev_names)),
        removed_line_names=tuple(name_by_key[n] for n in sorted(prev_names - curr_names)),
    )


def progression(results: list[PricingResult]) -> list[OfferDelta]:
    """Version-to-version movement across a dealer's whole offer history."""
    ordered = sorted(results, key=lambda r: r.version)
    return [diff(a, b) for a, b in zip(ordered, ordered[1:], strict=False)]
