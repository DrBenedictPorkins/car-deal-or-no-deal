"""Dealer behavior scoring.

The brief is explicit: no arbitrary AI scores. Every number here is a deterministic
function of recorded ``behavior_signal`` rows and message timing, and every report
carries the list of reasons that produced it. There is no composite "deal score"
anywhere in this product — price and behavior stay separate axes.

Friction is reported as *points* rather than an inverted score, because "85% friction"
reads ambiguously and a list of the actual obstacles is more useful than a number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

from app.enums import BehaviorDimension
from app.services.context import DealerContext

# Closed vocabulary. An unknown code is a bug, not a new category.
FRICTION_CODES: dict[str, str] = {
    "REFUSED_WRITTEN_QUOTE": "Refused to put a quote in writing",
    "INSISTS_ON_PHONE_CALL": "Insists on a phone call before pricing",
    "INSISTS_ON_VISIT": "Insists on a showroom visit before pricing",
    "REPEATED_PHONE_REQUEST": "Repeatedly asked for a phone number",
    "WITHHELD_OTD": "Would not give an out-the-door number",
    "CHANGED_VEHICLE": "Switched the vehicle under discussion",
    "INTRODUCED_ADD_ON": "Introduced a dealer add-on",
    "FINANCING_CONDITIONED_PRICE": "Price conditioned on dealer financing",
    "UNEXPLAINED_FEE": "Quote contains an unexplained charge",
    "SLOW_RESPONSE": "Slow to respond",
    "AUTOMATED_ONLY_CONTACT": "Only automated messages received",
}

POSITIVE_CODES: dict[str, str] = {
    "GAVE_ITEMIZED_OTD": "Provided an itemized out-the-door quote",
    "ANSWERED_DIRECT_QUESTION": "Answered direct questions",
    "PROVIDED_VIN": "Identified the VIN",
    "CONFIRMED_MILEAGE": "Confirmed mileage",
    "CONFIRMED_DEMO_STATUS": "Confirmed demo/loaner status",
    "CONFIRMED_FINANCING_TERMS": "Confirmed financing conditions",
    "WRITTEN_FINAL_PRICE": "Put a final price in writing",
    "FAST_RESPONSE": "Responded quickly",
    "PROACTIVE_UPDATE": "Proactively sent an update",
}

ALL_CODES = {**FRICTION_CODES, **POSITIVE_CODES}


@dataclass
class ScoredDimension:
    dimension: str
    score: int | None
    reasons: list[str] = field(default_factory=list)
    basis: str | None = None


@dataclass
class BehaviorReport:
    dealer_id: int
    transparency: ScoredDimension
    responsiveness: ScoredDimension
    price_competitiveness: ScoredDimension
    friction_points: float
    friction_reasons: list[str] = field(default_factory=list)

    @property
    def friction_label(self) -> str:
        if self.friction_points == 0:
            return "None"
        if self.friction_points <= 2:
            return "Low"
        if self.friction_points <= 5:
            return "Moderate"
        return "High"


def _label(code: str, detail: str | None) -> str:
    base = ALL_CODES.get(code, code.replace("_", " ").title())
    return f"{base} — {detail}" if detail else base


def median_response_hours(ctx: DealerContext) -> float | None:
    """Median time from one of the buyer's messages to the dealer's next reply."""
    gaps: list[float] = []
    inbound = sorted(ctx.inbound, key=lambda i: i.occurred_at)
    for out in sorted(ctx.outbound, key=lambda i: i.occurred_at):
        reply = next((i for i in inbound if i.occurred_at > out.occurred_at), None)
        if reply is not None:
            gaps.append((reply.occurred_at - out.occurred_at).total_seconds() / 3600)
    return median(gaps) if gaps else None


def _responsiveness(ctx: DealerContext) -> ScoredDimension:
    hours = median_response_hours(ctx)
    reasons: list[str] = []
    if hours is None:
        if ctx.outbound and not ctx.inbound:
            return ScoredDimension(
                BehaviorDimension.RESPONSIVENESS,
                0,
                ["Never replied to any of your messages"],
                basis="no replies",
            )
        return ScoredDimension(
            BehaviorDimension.RESPONSIVENESS, None, ["Not enough exchanges to judge"]
        )

    # Deterministic bands rather than a curve, so the number is explainable.
    for threshold, score, phrase in (
        (4, 100, "within 4 hours"),
        (12, 85, "within half a day"),
        (24, 70, "within a day"),
        (48, 55, "within two days"),
        (96, 35, "within four days"),
    ):
        if hours <= threshold:
            reasons.append(f"Typically replies {phrase} (median {hours:.1f}h)")
            return ScoredDimension(
                BehaviorDimension.RESPONSIVENESS, score, reasons, basis=f"median {hours:.1f}h"
            )
    reasons.append(f"Typically takes over four days to reply (median {hours:.1f}h)")
    return ScoredDimension(
        BehaviorDimension.RESPONSIVENESS, 15, reasons, basis=f"median {hours:.1f}h"
    )


def _transparency(ctx: DealerContext) -> ScoredDimension:
    score = 50
    reasons: list[str] = []

    pricing_result = ctx.current_pricing
    if pricing_result is not None:
        if pricing_result.quoted_otd_cents is not None:
            score += 20
            reasons.append("Gave a written out-the-door number (+20)")
        else:
            score -= 15
            reasons.append("Has not given an out-the-door number (-15)")
        if pricing_result.otd_variance_cents:
            score -= 15
            reasons.append("Quoted OTD does not reconcile with the line items (-15)")
        if pricing_result.lines:
            score += 10
            reasons.append("Quote is itemized (+10)")
    else:
        score -= 20
        reasons.append("No priced offer at all (-20)")

    for signal in ctx.signals:
        if signal.dimension != BehaviorDimension.TRANSPARENCY:
            continue
        delta = int(signal.polarity * signal.weight * 8)
        score += delta
        reasons.append(f"{_label(signal.code, signal.detail)} ({delta:+d})")

    if any(i.actor_kind == "AUTOMATED" for i in ctx.inbound) and not any(
        i.actor_kind == "HUMAN" for i in ctx.inbound
    ):
        score -= 20
        reasons.append("Every inbound message looks automated (-20)")

    return ScoredDimension(
        BehaviorDimension.TRANSPARENCY, max(0, min(100, score)), reasons, basis="signals"
    )


def _friction(ctx: DealerContext) -> tuple[float, list[str]]:
    points = 0.0
    reasons: list[str] = []
    for signal in ctx.signals:
        if signal.dimension != BehaviorDimension.FRICTION:
            continue
        points += signal.weight
        reasons.append(_label(signal.code, signal.detail))

    pricing_result = ctx.current_pricing
    if pricing_result is not None:
        if pricing_result.otd_variance_cents:
            points += 1
            reasons.append(
                f"Quote contains an unexplained charge of "
                f"{abs(pricing_result.otd_variance_cents) / 100:,.2f} dollars"
            )
        unwanted = [line for line in pricing_result.lines if not line.user_wants and
                    line.kind == "ADD_ON"]
        if unwanted:
            points += 1
            reasons.append(
                "Quote includes add-ons you do not want: "
                + ", ".join(line.name for line in unwanted)
            )
        offer = ctx.current_offer
        if offer is not None and offer.financing_required:
            points += 1
            reasons.append("Price is conditioned on dealer financing")
    return points, reasons


def report(
    ctx: DealerContext, *, price_rank: tuple[int, int] | None = None
) -> BehaviorReport:
    """``price_rank`` is (rank, total) among dealers with a comparable offer."""
    friction_points, friction_reasons = _friction(ctx)

    if price_rank is None:
        price = ScoredDimension(
            BehaviorDimension.PRICE_COMPETITIVENESS, None, ["No comparable offer yet"]
        )
    else:
        rank, total = price_rank
        score = 100 if total <= 1 else round(100 - (rank - 1) * 100 / (total - 1))
        price = ScoredDimension(
            BehaviorDimension.PRICE_COMPETITIVENESS,
            score,
            [f"Ranked {rank} of {total} on dealer-controlled cost"],
            basis=f"rank {rank}/{total}",
        )

    return BehaviorReport(
        dealer_id=ctx.dealer.id,
        transparency=_transparency(ctx),
        responsiveness=_responsiveness(ctx),
        price_competitiveness=price,
        friction_points=friction_points,
        friction_reasons=friction_reasons,
    )


def derive_signals(ctx: DealerContext) -> list[dict]:
    """Rule-derived signals from the data we already have.

    Returned as plain dicts so the caller decides whether to persist. Keyed by a
    stable dedupe key so re-running detection updates rather than duplicates.
    """
    out: list[dict] = []
    dealer_id = ctx.dealer.id

    p = ctx.current_pricing
    if p is not None:
        if p.quoted_otd_cents is not None and p.lines:
            out.append(
                {
                    "dealer_id": dealer_id,
                    "dimension": BehaviorDimension.TRANSPARENCY,
                    "code": "GAVE_ITEMIZED_OTD",
                    "polarity": 1,
                    "weight": 1.5,
                    "detail": f"Offer v{p.version}",
                    "dedupe_key": f"{dealer_id}:GAVE_ITEMIZED_OTD:{p.offer_id}",
                }
            )
        if p.otd_variance_cents:
            out.append(
                {
                    "dealer_id": dealer_id,
                    "dimension": BehaviorDimension.FRICTION,
                    "code": "UNEXPLAINED_FEE",
                    "polarity": -1,
                    "weight": 1.0,
                    "detail": f"{abs(p.otd_variance_cents) / 100:,.2f} dollars unaccounted for",
                    "dedupe_key": f"{dealer_id}:UNEXPLAINED_FEE:{p.offer_id}",
                }
            )
        for line in p.lines:
            if line.kind == "ADD_ON" and not line.user_wants:
                out.append(
                    {
                        "dealer_id": dealer_id,
                        "dimension": BehaviorDimension.FRICTION,
                        "code": "INTRODUCED_ADD_ON",
                        "polarity": -1,
                        "weight": 0.5,
                        "detail": f"{line.name} at {line.price_cents / 100:,.2f} dollars",
                        "dedupe_key": f"{dealer_id}:INTRODUCED_ADD_ON:{p.offer_id}:{line.id}",
                    }
                )

    offer = ctx.current_offer
    if offer is not None and offer.financing_required:
        out.append(
            {
                "dealer_id": dealer_id,
                "dimension": BehaviorDimension.FRICTION,
                "code": "FINANCING_CONDITIONED_PRICE",
                "polarity": -1,
                "weight": 1.0,
                "detail": offer.financing_provider or "unspecified lender",
                "dedupe_key": f"{dealer_id}:FINANCING_CONDITIONED_PRICE:{offer.id}",
            }
        )

    if ctx.inbound and all(i.actor_kind == "AUTOMATED" for i in ctx.inbound):
        out.append(
            {
                "dealer_id": dealer_id,
                "dimension": BehaviorDimension.FRICTION,
                "code": "AUTOMATED_ONLY_CONTACT",
                "polarity": -1,
                "weight": 1.0,
                "detail": f"{len(ctx.inbound)} inbound messages, none identified as human",
                "dedupe_key": f"{dealer_id}:AUTOMATED_ONLY_CONTACT:{len(ctx.inbound)}",
            }
        )

    hours = median_response_hours(ctx)
    if hours is not None and hours <= 4:
        out.append(
            {
                "dealer_id": dealer_id,
                "dimension": BehaviorDimension.RESPONSIVENESS,
                "code": "FAST_RESPONSE",
                "polarity": 1,
                "weight": 1.0,
                "detail": f"median {hours:.1f}h",
                "dedupe_key": f"{dealer_id}:FAST_RESPONSE:{len(ctx.inbound)}",
            }
        )
    elif hours is not None and hours > 72:
        out.append(
            {
                "dealer_id": dealer_id,
                "dimension": BehaviorDimension.FRICTION,
                "code": "SLOW_RESPONSE",
                "polarity": -1,
                "weight": 0.5,
                "detail": f"median {hours:.1f}h",
                "dedupe_key": f"{dealer_id}:SLOW_RESPONSE:{len(ctx.inbound)}",
            }
        )

    # A dealer who only ever sends outbound-triggered replies with no price and a
    # request to call is the "phone friction" pattern from the source workflow.
    for interaction in ctx.inbound:
        text = (interaction.normalized_content or "").lower()
        if not text:
            continue
        if any(
            phrase in text
            for phrase in ("give me a call", "call me", "best time to reach", "phone number")
        ):
            out.append(
                {
                    "dealer_id": dealer_id,
                    "dimension": BehaviorDimension.FRICTION,
                    "code": "INSISTS_ON_PHONE_CALL",
                    "polarity": -1,
                    "weight": 1.0,
                    "interaction_id": interaction.id,
                    "observed_at": interaction.occurred_at,
                    "detail": interaction.subject or "asked to move to a phone call",
                    "dedupe_key": f"{dealer_id}:INSISTS_ON_PHONE_CALL:{interaction.id}",
                }
            )
        if any(
            phrase in text
            for phrase in ("come in", "stop by", "visit the showroom", "in person", "come down")
        ):
            out.append(
                {
                    "dealer_id": dealer_id,
                    "dimension": BehaviorDimension.FRICTION,
                    "code": "INSISTS_ON_VISIT",
                    "polarity": -1,
                    "weight": 1.0,
                    "interaction_id": interaction.id,
                    "observed_at": interaction.occurred_at,
                    "detail": interaction.subject or "asked you to come to the dealership",
                    "dedupe_key": f"{dealer_id}:INSISTS_ON_VISIT:{interaction.id}",
                }
            )

    if ctx.inbound and ctx.current_offer is None and len(ctx.inbound) >= 2:
        out.append(
            {
                "dealer_id": dealer_id,
                "dimension": BehaviorDimension.FRICTION,
                "code": "WITHHELD_OTD",
                "polarity": -1,
                "weight": 1.5,
                "detail": f"{len(ctx.inbound)} replies, still no priced offer",
                "dedupe_key": f"{dealer_id}:WITHHELD_OTD:{len(ctx.inbound)}",
            }
        )

    # Deduplicate within the batch — a message can trip the same rule twice.
    seen: set[str] = set()
    unique: list[dict] = []
    for item in out:
        key = item["dedupe_key"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique
