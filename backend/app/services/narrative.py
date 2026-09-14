"""Deterministic negotiation summary.

Phase 1 has no LLM, and a summary assembled from structured state is more trustworthy
than one assembled from prose anyway. When Phase 3 adds an LLM summary it goes *beside*
this one, not in place of it — this text is generated from facts the user can click
through to, which is the whole point.
"""

from __future__ import annotations

from app.enums import Party
from app.services.context import DealerContext
from app.services.money import bp_to_percent_str, fmt
from app.services.pricing import progression


def summarize(ctx: DealerContext) -> str:
    dealer = ctx.dealer
    parts: list[str] = []

    if not ctx.interactions:
        return f"No contact with {dealer.name} yet."

    inbound, outbound = len(ctx.inbound), len(ctx.outbound)
    parts.append(
        f"{outbound} message(s) sent to {dealer.name}, {inbound} received"
        + (f" over {ctx.idle_days:.0f} days of quiet since the last one" if ctx.idle_days
           and ctx.idle_days >= 1 else "")
        + "."
    )

    automated = [i for i in ctx.inbound if i.actor_kind == "AUTOMATED"]
    if automated and len(automated) == inbound and inbound:
        parts.append(
            f"All {inbound} inbound message(s) look automated rather than written by a "
            f"person."
        )
    elif automated:
        parts.append(f"{len(automated)} of the inbound messages look automated.")

    history = ctx.offer_history
    if not history:
        parts.append("No priced offer has been received.")
    else:
        first, last = history[0], history[-1]
        parts.append(
            f"Offer v{last.version} stands at {fmt(last.effective_otd_cents)} out the door "
            f"({fmt(last.dealer_controlled_cents)} once tax and registration are set aside)."
        )
        if last.discount_from_msrp_cents:
            parts.append(
                f"That is {fmt(last.discount_from_msrp_cents)} off an MSRP of "
                f"{fmt(last.msrp_cents)}."
            )
        deltas = progression(history)
        total_move = sum(d.otd_delta_cents or 0 for d in deltas)
        if total_move:
            direction = "down" if total_move < 0 else "up"
            parts.append(
                f"They have moved {direction} {fmt(abs(total_move))} from their opening "
                f"{fmt(first.effective_otd_cents)} across {len(history)} quotes."
            )
        if last.otd_variance_cents:
            parts.append(
                f"Their quoted OTD is {fmt(abs(last.otd_variance_cents))} "
                f"{'above' if last.otd_variance_cents > 0 else 'below'} the sum of the "
                f"line items they gave — worth asking about."
            )
        offer = ctx.current_offer
        if offer is not None and offer.financing_required:
            terms = []
            if offer.financing_provider:
                terms.append(offer.financing_provider)
            if offer.apr_bp is not None:
                terms.append(f"{bp_to_percent_str(offer.apr_bp)} APR")
            if offer.financing_term_months:
                terms.append(f"{offer.financing_term_months} months")
            parts.append(
                "This price requires dealer financing"
                + (f" ({', '.join(terms)})" if terms else "")
                + "."
            )
            unknowns = [
                label
                for label, value in (
                    ("prepayment penalty", offer.prepayment_penalty),
                    ("discount clawback", offer.discount_clawback),
                )
                if value is None
            ]
            if unknowns:
                parts.append(
                    "Do not plan on financing and paying it off immediately until the "
                    + " and ".join(unknowns)
                    + " terms are confirmed in writing."
                )

    owes, reason = ctx.owes_response()
    if owes == Party.DEALER:
        parts.append(f"The dealer owes the next response — {reason.lower()}")
    elif owes == Party.BUYER:
        parts.append(f"You owe the next response — {reason.lower()}")

    if ctx.open_buyer_questions:
        parts.append(
            f"{len(ctx.open_buyer_questions)} question(s) you asked remain unanswered."
        )
    if ctx.open_contradictions:
        parts.append(
            f"{len(ctx.open_contradictions)} unresolved contradiction(s) on record."
        )
    return " ".join(parts)
