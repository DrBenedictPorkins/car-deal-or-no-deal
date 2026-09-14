"""Side-by-side dealer comparison.

Four winners are identified independently — cheapest OTD, cheapest dealer-controlled
cost, most convenient, cleanest offer — because collapsing them into one opaque score
is exactly what the brief forbids, and because they genuinely disagree: the cheapest
OTD can be the one with a $995 ceramic coating on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.services import behavior
from app.services.context import DealerContext, build_contexts
from app.services.money import fmt


@dataclass
class ComparisonRow:
    dealer_id: int
    dealer_name: str
    is_local: bool
    distance_miles: float | None
    state_code: str

    vehicle_summary: str | None
    vin: str | None
    exterior_color: str | None
    mileage: int | None
    is_demo: bool | None

    msrp_cents: int | None
    selling_price_cents: int | None
    discount_from_msrp_cents: int | None
    dealer_fees_total_cents: int | None
    fees_disclosed: bool
    add_ons_total_cents: int | None
    dealer_controlled_cents: int | None
    clean_dealer_controlled_cents: int | None
    tax_cents: int | None
    registration_cents: int | None
    government_total_cents: int | None
    otd_cents: int | None
    otd_variance_cents: int | None

    financing_required: bool | None
    financing_provider: str | None
    apr_bp: int | None
    financing_term_months: int | None

    friction_points: float
    friction_reasons: list[str]
    unresolved_issues: list[str]
    cleanliness_score: int
    cleanliness_reasons: list[str]

    offer_version: int | None
    quoted_at: str | None


@dataclass
class Winner:
    label: str
    dealer_id: int | None
    dealer_name: str | None
    value: str
    explanation: str


@dataclass
class ComparisonResult:
    rows: list[ComparisonRow] = field(default_factory=list)
    winners: list[Winner] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _unresolved(ctx: DealerContext) -> list[str]:
    issues: list[str] = []
    p = ctx.current_pricing
    if p is None:
        issues.append("No priced offer")
    else:
        if p.quoted_otd_cents is None:
            issues.append("No written OTD")
        if not p.fees_disclosed:
            issues.append("No dealer fees disclosed — their cost is a floor, not a figure")
        if p.otd_variance_cents:
            issues.append(f"{fmt(abs(p.otd_variance_cents))} unexplained in the quote")
        if p.tax_rate_variance_bp is not None and abs(p.tax_rate_variance_bp) >= 10:
            issues.append("Tax does not match the expected rate")
        unwanted = [line.name for line in p.lines if line.kind == "ADD_ON" and not line.user_wants]
        if unwanted:
            issues.append("Unwanted add-ons: " + ", ".join(unwanted))
    offer = ctx.current_offer
    if offer is not None:
        if offer.financing_required:
            issues.append("Price requires dealer financing")
        if offer.financing_required and offer.discount_clawback is None:
            issues.append("Clawback terms unconfirmed")
        if offer.financing_required and offer.prepayment_penalty is None:
            issues.append("Prepayment penalty unconfirmed")
    for q in ctx.open_buyer_questions:
        issues.append(f"Unanswered: {q.text}")
    for c in ctx.open_contradictions:
        issues.append(f"Contradiction: {c.summary}")
    vehicle_ids = {o.vehicle_id for o in ctx.offers if o.vehicle_id}
    if len(vehicle_ids) > 1:
        issues.append("Quotes cover more than one vehicle")
    return issues


def _cleanliness(ctx: DealerContext) -> tuple[int, list[str]]:
    """How free of strings the offer is. Deterministic, fully itemized."""
    score = 100
    reasons: list[str] = []
    p = ctx.current_pricing
    if p is None:
        return 0, ["No offer to evaluate"]

    if p.quoted_otd_cents is None:
        score -= 30
        reasons.append("No written OTD (-30)")
    if p.otd_variance_cents:
        score -= 20
        reasons.append(f"{fmt(abs(p.otd_variance_cents))} unexplained (-20)")
    if not p.fees_disclosed:
        score -= 15
        reasons.append("No dealer fees disclosed (-15)")
    unwanted = [line for line in p.lines if line.kind == "ADD_ON" and not line.user_wants]
    if unwanted:
        penalty = min(30, 10 * len(unwanted))
        score -= penalty
        reasons.append(
            f"{len(unwanted)} unwanted add-on(s) worth {fmt(p.unwanted_add_ons_cents)} "
            f"(-{penalty})"
        )
    offer = ctx.current_offer
    if offer is not None and offer.financing_required:
        score -= 20
        reasons.append("Price conditioned on financing (-20)")
    if ctx.open_buyer_questions:
        score -= 10
        reasons.append(f"{len(ctx.open_buyer_questions)} unanswered question(s) (-10)")
    if ctx.open_contradictions:
        score -= 15
        reasons.append(f"{len(ctx.open_contradictions)} open contradiction(s) (-15)")
    if not reasons:
        reasons.append("Itemized written OTD with no strings attached")
    return max(0, score), reasons


def _vehicle_summary(ctx: DealerContext):
    offer = ctx.current_offer
    vehicle = None
    if offer is not None and offer.vehicle_id is not None:
        vehicle = next((v for v in ctx.dealer.vehicles if v.id == offer.vehicle_id), None)
    if vehicle is None and ctx.dealer.vehicles:
        vehicle = ctx.dealer.vehicles[0]
    if vehicle is None:
        return None, None, None, None, None
    parts = [str(vehicle.year or ""), vehicle.make or "", vehicle.model or "", vehicle.trim or ""]
    summary = " ".join(p for p in parts if p).strip() or None
    return summary, vehicle.vin, vehicle.exterior_color, vehicle.mileage, vehicle.is_demo


def build_rows(contexts: list[DealerContext]) -> list[ComparisonRow]:
    # Price rank is computed across the whole set so each row can report its position.
    with_cost = [
        (c, c.current_pricing.dealer_controlled_cents)
        for c in contexts
        if c.current_pricing and c.current_pricing.dealer_controlled_cents is not None
    ]
    with_cost.sort(key=lambda pair: pair[1])
    ranks = {c.dealer.id: (i + 1, len(with_cost)) for i, (c, _) in enumerate(with_cost)}

    rows: list[ComparisonRow] = []
    for ctx in contexts:
        p = ctx.current_pricing
        offer = ctx.current_offer
        issues = _unresolved(ctx)
        clean_score, clean_reasons = _cleanliness(ctx)
        report = behavior.report(ctx, price_rank=ranks.get(ctx.dealer.id))
        summary, vin, color, mileage, is_demo = _vehicle_summary(ctx)

        rows.append(
            ComparisonRow(
                dealer_id=ctx.dealer.id,
                dealer_name=ctx.dealer.name,
                is_local=ctx.dealer.is_local,
                distance_miles=float(ctx.dealer.distance_miles)
                if ctx.dealer.distance_miles is not None
                else None,
                state_code=ctx.dealer.state_code,
                vehicle_summary=summary,
                vin=vin,
                exterior_color=color,
                mileage=mileage,
                is_demo=is_demo,
                msrp_cents=p.msrp_cents if p else None,
                selling_price_cents=p.selling_price_cents if p else None,
                discount_from_msrp_cents=p.discount_from_msrp_cents if p else None,
                dealer_fees_total_cents=p.dealer_fees_total_cents if p else None,
                fees_disclosed=bool(p and p.fees_disclosed),
                add_ons_total_cents=p.add_ons_total_cents if p else None,
                dealer_controlled_cents=p.dealer_controlled_cents if p else None,
                clean_dealer_controlled_cents=p.clean_dealer_controlled_cents if p else None,
                tax_cents=offer.tax_cents if offer else None,
                registration_cents=offer.registration_cents if offer else None,
                government_total_cents=(
                    p.government_total_cents if p and p.government_disclosed else None
                ),
                otd_cents=p.effective_otd_cents if p else None,
                otd_variance_cents=p.otd_variance_cents if p else None,
                financing_required=offer.financing_required if offer else None,
                financing_provider=offer.financing_provider if offer else None,
                apr_bp=offer.apr_bp if offer else None,
                financing_term_months=offer.financing_term_months if offer else None,
                friction_points=report.friction_points,
                friction_reasons=report.friction_reasons,
                unresolved_issues=issues,
                cleanliness_score=clean_score,
                cleanliness_reasons=clean_reasons,
                offer_version=offer.version if offer else None,
                quoted_at=offer.quoted_at.isoformat() if offer else None,
            )
        )
    return rows


def _winner(rows: list[ComparisonRow], label: str, key, value_fmt, explanation) -> Winner:
    eligible = [r for r in rows if key(r) is not None]
    if not eligible:
        return Winner(label=label, dealer_id=None, dealer_name=None, value="—",
                      explanation="No dealer has enough information yet.")
    best = min(eligible, key=key)
    return Winner(
        label=label,
        dealer_id=best.dealer_id,
        dealer_name=best.dealer_name,
        value=value_fmt(best),
        explanation=explanation(best, eligible),
    )


def compare(db: Session, dealer_ids: list[int] | None = None) -> ComparisonResult:
    contexts = list(build_contexts(db, dealer_ids=dealer_ids).values())
    rows = build_rows(contexts)
    rows.sort(key=lambda r: (r.otd_cents is None, r.otd_cents or 0))

    winners: list[Winner] = []

    def runner_up_gap(best, eligible, key) -> str:
        others = sorted(key(r) for r in eligible if r.dealer_id != best.dealer_id)
        if not others:
            return "only dealer with a comparable number"
        return f"{fmt(others[0] - key(best))} better than the next best"

    winners.append(
        _winner(
            rows,
            "Cheapest OTD",
            lambda r: r.otd_cents,
            lambda r: fmt(r.otd_cents),
            lambda best, elig: runner_up_gap(best, elig, lambda r: r.otd_cents),
        )
    )
    winners.append(
        _winner(
            rows,
            "Cheapest dealer-controlled cost",
            lambda r: r.dealer_controlled_cents,
            lambda r: fmt(r.dealer_controlled_cents),
            lambda best, elig: runner_up_gap(best, elig, lambda r: r.dealer_controlled_cents)
            + " once tax and registration are set aside",
        )
    )
    # Convenience is only meaningful among dealers who actually quoted something. A
    # dealer who sent no price at all is not "convenient", it is absent.
    winners.append(
        _winner(
            [r for r in rows if r.dealer_controlled_cents is not None],
            "Most convenient",
            lambda r: (
                r.friction_points,
                r.distance_miles if r.distance_miles is not None else 1e6,
            ),
            lambda r: f"{r.friction_points:g} friction point(s)",
            lambda best, elig: (
                f"{best.friction_points:g} friction point(s)"
                + (f", {best.distance_miles:g} miles away" if best.distance_miles else "")
            ),
        )
    )
    winners.append(
        _winner(
            rows,
            "Cleanest offer",
            lambda r: -r.cleanliness_score,
            lambda r: f"{r.cleanliness_score}/100",
            lambda best, elig: "; ".join(best.cleanliness_reasons[:2]),
        )
    )

    notes: list[str] = []
    variances = [r for r in rows if r.otd_variance_cents]
    for row in variances:
        notes.append(
            f"{row.dealer_name}: quoted OTD is {fmt(abs(row.otd_variance_cents))} "
            f"{'above' if row.otd_variance_cents > 0 else 'below'} the sum of its line items. "
            f"Ranking uses the quoted figure."
        )
    missing = [r.dealer_name for r in rows if r.otd_cents is None]
    if missing:
        notes.append("No out-the-door number from: " + ", ".join(missing))

    return ComparisonResult(rows=rows, winners=winners, notes=notes)
