"""Rule-based "what should I do next".

Phase 1 has no LLM. In Phase 3 the model will *narrate* these conclusions and draft the
message that carries them out — but the rule still chooses the move, because the choice
depends on arithmetic (is this dealer cheaper than the best competitor, and by enough to
outweigh the local-dealer premium?) and arithmetic does not belong in a model.

Every recommendation carries the numbers it was based on, so the advice is auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.enums import Party, Severity
from app.services.context import DealerContext
from app.services.money import fmt


@dataclass
class Recommendation:
    code: str
    headline: str
    detail: str
    priority: int  # lower sorts first on the dashboard
    severity: str = Severity.INFO
    supporting_numbers: dict[str, str] = field(default_factory=dict)
    suggested_message: str | None = None


@dataclass
class Benchmark:
    """The best competing offer, used as negotiating leverage."""

    dealer_id: int | None
    dealer_name: str | None
    otd_cents: int | None
    dealer_controlled_cents: int | None
    has_written_otd: bool = False


def best_benchmark(
    contexts: list[DealerContext], *, excluding_dealer_id: int | None = None
) -> Benchmark:
    """Cheapest OTD among *other* dealers, preferring offers actually in writing."""
    candidates = [
        c
        for c in contexts
        if c.dealer.id != excluding_dealer_id
        and c.current_pricing is not None
        and c.current_pricing.effective_otd_cents is not None
    ]
    if not candidates:
        return Benchmark(None, None, None, None)

    written = [c for c in candidates if c.has_written_otd]
    pool = written or candidates
    best = min(pool, key=lambda c: c.current_pricing.effective_otd_cents)
    p = best.current_pricing
    return Benchmark(
        dealer_id=best.dealer.id,
        dealer_name=best.dealer.name,
        otd_cents=p.effective_otd_cents,
        dealer_controlled_cents=p.dealer_controlled_cents,
        has_written_otd=best.has_written_otd,
    )


def accepted_dealer(contexts: list[DealerContext]) -> str | None:
    """Name of the dealer the buyer has accepted, if any.

    Once a deal is done, "ask them to beat $30,500" is the wrong advice for every other
    dealer on the board — they need closing out, not negotiating with.
    """
    for ctx in contexts:
        if ctx.dealer.state_code == "ACCEPTED":
            return ctx.dealer.name
    return None


def _local_premium(ctx: DealerContext) -> int:
    return ctx.profile.local_dealer_premium_cents if ctx.profile else 0


def recommend(
    ctx: DealerContext, benchmark: Benchmark, *, deal_accepted_with: str | None = None
) -> Recommendation:
    dealer = ctx.dealer
    p = ctx.current_pricing
    owes, owes_reason = ctx.owes_response()
    numbers: dict[str, str] = {}
    if p and p.effective_otd_cents is not None:
        numbers["This dealer's OTD"] = fmt(p.effective_otd_cents)
    if p and p.dealer_controlled_cents is not None:
        numbers["Dealer-controlled cost"] = fmt(p.dealer_controlled_cents)
    if benchmark.otd_cents is not None:
        numbers[f"Best competing OTD ({benchmark.dealer_name})"] = fmt(benchmark.otd_cents)

    # --- terminal states ---------------------------------------------------
    if ctx.is_terminal:
        if dealer.state_code == "ACCEPTED":
            return Recommendation(
                "ARRANGE_PICKUP",
                "Arrange pickup and confirm the paperwork",
                "Deal accepted. Confirm the final buyer's order matches the agreed numbers "
                "line for line before you sign anything.",
                priority=5,
                supporting_numbers=numbers,
            )
        return Recommendation(
            "NO_ACTION",
            "No action needed",
            f"This dealer is {dealer.state_code.replace('_', ' ').lower()}.",
            priority=900,
            supporting_numbers=numbers,
        )

    # --- a deal has already been struck elsewhere ---------------------------
    if deal_accepted_with:
        return Recommendation(
            "CLOSE_OUT",
            f"Close this one out — you've accepted {deal_accepted_with}",
            (
                "Send a short, polite note saying you bought elsewhere. Saying why "
                "(direct written pricing, no phone call required) is worth doing: it is "
                "the only feedback these dealers get."
            ),
            priority=400,
            supporting_numbers=numbers,
            suggested_message=(
                "Thanks for your time. I purchased from another dealership that responded "
                "to my request with direct written pricing without requiring a phone call "
                "or dealership visit."
            ),
        )

    # --- deadlines outrank everything else ---------------------------------
    offer = ctx.current_offer
    if offer is not None and offer.expires_at is not None:
        hours_left = (offer.expires_at - ctx.now).total_seconds() / 3600
        if 0 <= hours_left <= 48:
            return Recommendation(
                "DEADLINE",
                f"Offer expires in {hours_left:.0f} hours",
                (
                    f"{dealer.name}'s offer of {fmt(p.effective_otd_cents) if p else '—'} "
                    f"expires {offer.expires_at:%b %d %H:%M}"
                    + (f" — {offer.deadline_note}" if offer.deadline_note else "")
                    + ". Decide or ask for an extension before it lapses."
                ),
                priority=1,
                severity=Severity.CRITICAL,
                supporting_numbers=numbers,
            )

    # --- open contradictions ------------------------------------------------
    if ctx.open_contradictions:
        first = ctx.open_contradictions[0]
        return Recommendation(
            "RESOLVE_CONTRADICTION",
            "Get the dealer to reconcile conflicting statements",
            f"{first.summary} Ask them, in writing, which one is correct.",
            priority=15,
            severity=Severity.WARNING,
            supporting_numbers=numbers,
        )

    # --- no contact yet ------------------------------------------------------
    # An offer on file means contact happened, even if the carrying message was never
    # ingested (read over the phone, dropped in as a PDF).
    if not ctx.interactions and ctx.current_offer is None:
        return Recommendation(
            "SEND_INITIAL_INQUIRY",
            "Send the initial inquiry",
            "Ask for an itemized out-the-door price on a specific VIN, in writing, "
            "with no add-ons.",
            priority=40,
            supporting_numbers=numbers,
        )

    # --- dealer has gone dark ------------------------------------------------
    if dealer.state_code == "NO_RESPONSE":
        days = ctx.idle_days or 0
        return Recommendation(
            "FINAL_FOLLOW_UP",
            f"No reply in {days:.0f} days — send one final note or drop them",
            "One short follow-up with a deadline, then stop spending time here.",
            priority=300,
            supporting_numbers=numbers,
        )

    # --- dealer owes a response ---------------------------------------------
    if owes == Party.DEALER:
        idle_hours = ctx.idle_hours or 0
        threshold = ctx.profile.follow_up_after_hours if ctx.profile else 48
        if idle_hours >= threshold:
            return Recommendation(
                "FOLLOW_UP",
                f"Follow up — dealer has owed a reply for {idle_hours / 24:.0f} days",
                f"{owes_reason} A short nudge with a specific ask works better than "
                f"a general check-in.",
                priority=60,
                severity=Severity.WARNING,
                supporting_numbers=numbers,
            )
        return Recommendation(
            "WAIT",
            "Wait for the dealer's reply",
            f"{owes_reason} Nudge after "
            f"{max(0, threshold - idle_hours):.0f} more hours if nothing arrives.",
            priority=200,
            supporting_numbers=numbers,
        )

    # --- buyer owes a response, no offer yet --------------------------------
    if p is None or p.effective_otd_cents is None:
        friction = [
            s.code
            for s in ctx.signals
            if s.code in ("INSISTS_ON_PHONE_CALL", "INSISTS_ON_VISIT", "REFUSED_WRITTEN_QUOTE")
        ]
        if friction:
            return Recommendation(
                "RESTATE_WRITTEN_ONLY",
                "Restate that you need written pricing before anything else",
                "They are steering you to a call or a visit. Reply once, politely, "
                "restating that you will buy from whoever sends an itemized OTD in "
                "writing — and mention that other dealers already have.",
                priority=80,
                supporting_numbers=numbers,
            )
        return Recommendation(
            "REQUEST_WRITTEN_OTD",
            "Ask for an itemized out-the-door price",
            "Request MSRP, selling price, every dealer fee, every add-on, tax and "
            "registration as separate lines, for a specific VIN.",
            priority=70,
            supporting_numbers=numbers,
        )

    # --- buyer owes a response and there is an offer to judge ---------------
    otd = p.effective_otd_cents
    premium = _local_premium(ctx)

    if benchmark.otd_cents is None:
        return Recommendation(
            "PUSH_FIRST_OFFER",
            "Push back on the only offer you have",
            f"{fmt(otd)} is the only number on the table, so there is nothing to "
            f"benchmark it against. Get a second written quote before you accept, and "
            f"ask this dealer to remove anything you didn't ask for.",
            priority=90,
            supporting_numbers=numbers,
        )

    gap = otd - benchmark.otd_cents

    if gap <= 0:
        return Recommendation(
            "LOCK_IN",
            "This is your best offer — lock it in",
            f"{fmt(otd)} beats {benchmark.dealer_name} by {fmt(abs(gap))}. Ask them to "
            f"hold it in writing with a date, confirm the VIN, and confirm there are no "
            f"add-ons or financing conditions attached.",
            priority=20,
            supporting_numbers=numbers,
        )

    if dealer.is_local and gap <= premium:
        return Recommendation(
            "ACCEPT_LOCAL_PREMIUM",
            f"Worth paying {fmt(gap)} to buy locally",
            f"{fmt(otd)} is {fmt(gap)} above {benchmark.dealer_name}, but within the "
            f"{fmt(premium)} you said a local dealer is worth. Confirm the numbers in "
            f"writing and close.",
            priority=25,
            supporting_numbers=numbers,
        )

    target = benchmark.otd_cents
    local_note = (
        " Because this dealer is local, a small premium may still be reasonable — but "
        "make them earn it."
        if dealer.is_local
        else ""
    )
    return Recommendation(
        "ASK_TO_BEAT",
        f"Ask them to beat {fmt(target)} OTD",
        (
            f"{dealer.name} is at {fmt(otd)}, which is {fmt(gap)} more than "
            f"{benchmark.dealer_name}"
            + (" (written offer)" if benchmark.has_written_otd else "")
            + f". Send the competing number and ask them to beat it.{local_note}"
        ),
        priority=30,
        supporting_numbers=numbers,
        suggested_message=_beat_draft(ctx, target, benchmark),
    )


def _beat_draft(ctx: DealerContext, target_cents: int, benchmark: Benchmark) -> str:
    """A starting draft built from the structured state, not from the last email."""
    name = ctx.dealer.contacts[0].name if ctx.dealer.contacts else None
    greeting = f"Hi {name.split()[0]}," if name else "Hello,"
    local_line = (
        "I'd prefer to buy locally if you can beat it. "
        if ctx.dealer.is_local
        else "I'm ready to move on whichever offer is best. "
    )
    p = ctx.current_pricing
    unwanted = (
        [line.name for line in p.lines if line.kind == "ADD_ON" and not line.user_wants]
        if p
        else []
    )
    addon_line = (
        f"\n\nI also don't want {', '.join(unwanted)} — please quote without "
        f"{'them' if len(unwanted) > 1 else 'it'}."
        if unwanted
        else ""
    )
    return (
        f"{greeting}\n\n"
        f"Thanks for sending this over. I have a written offer at {fmt(target_cents)} "
        f"out the door on the same vehicle. {local_line}"
        f"If you can beat that number, I'll commit today.{addon_line}\n\n"
        f"Could you send an updated itemized out-the-door figure — selling price, "
        f"all dealer fees, any add-ons, tax and registration as separate lines?\n\n"
        f"Thanks."
    )
