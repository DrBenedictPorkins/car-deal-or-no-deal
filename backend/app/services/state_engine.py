"""Negotiation state rules.

Rules are declarative and evaluated in priority order; the first that matches wins and
supplies both the target state and a human-readable reason. Every change is appended to
``state_transition`` — the dealer's ``state_code`` column is only a cache of the latest
row.

Two things this deliberately does *not* do:

* It never leaves or enters a terminal state on its own. ACCEPTED / LOST / CLOSED are
  the user's judgement, not the engine's.
* It never silently overrides a pinned state. It records the transition it *would* have
  made, with ``was_suppressed_by_pin`` set, so the user can see the engine disagreeing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.enums import Direction
from app.models import StateTransition
from app.models.base import utcnow
from app.services.context import DealerContext, build_context, build_contexts


@dataclass(frozen=True)
class RuleOutcome:
    state: str
    reason_code: str
    reason_text: str


@dataclass(frozen=True)
class Rule:
    id: str
    priority: int
    evaluate: Callable[[DealerContext], RuleOutcome | None]


def _follow_up_hours(ctx: DealerContext) -> int:
    return ctx.profile.follow_up_after_hours if ctx.profile else 48


def _no_response_days(ctx: DealerContext) -> int:
    return ctx.profile.no_response_after_days if ctx.profile else 7


# --------------------------------------------------------------------------- rules


def _rule_no_contact(ctx: DealerContext) -> RuleOutcome | None:
    if ctx.interactions:
        return None
    return RuleOutcome("DISCOVERED", "NO_CONTACT", "No messages exchanged yet.")


def _rule_never_replied(ctx: DealerContext) -> RuleOutcome | None:
    # A recorded offer is itself proof the dealer engaged, even if the message that
    # carried it was never ingested (a quote read over the phone, a PDF dropped in).
    if ctx.inbound or ctx.current_offer is not None or not ctx.outbound:
        return None
    last_out = ctx.last_outbound
    assert last_out is not None
    hours = (ctx.now - last_out.occurred_at).total_seconds() / 3600
    days = hours / 24
    if days >= _no_response_days(ctx):
        return RuleOutcome(
            "NO_RESPONSE",
            "SILENT_TOO_LONG",
            f"No reply {days:.0f} days after you reached out.",
        )
    if hours >= _follow_up_hours(ctx):
        return RuleOutcome(
            "AWAITING_RESPONSE",
            "AWAITING_FIRST_REPLY",
            f"You reached out {hours:.0f} hours ago and the dealer has not replied.",
        )
    return RuleOutcome(
        "CONTACTED", "JUST_CONTACTED", "Inquiry sent; a reply is not overdue yet."
    )


def _rule_offer_on_table(ctx: DealerContext) -> RuleOutcome | None:
    offer = ctx.current_offer
    if offer is None:
        return None

    # Did the buyer respond after the most recent offer? That is a counter.
    later_outbound = [i for i in ctx.outbound if i.occurred_at > offer.quoted_at]
    if later_outbound:
        latest = max(later_outbound, key=lambda i: i.occurred_at)
        hours = (ctx.now - latest.occurred_at).total_seconds() / 3600
        if hours >= _follow_up_hours(ctx):
            return RuleOutcome(
                "AWAITING_COUNTER",
                "COUNTER_UNANSWERED",
                f"You countered {hours / 24:.0f} days ago with no reply.",
            )
        return RuleOutcome(
            "COUNTER_SENT", "COUNTER_SENT", "You countered the dealer's offer."
        )

    return RuleOutcome(
        "QUOTE_RECEIVED",
        "OFFER_RECEIVED",
        f"Offer v{offer.version} is on the table and awaiting your move.",
    )


def _rule_replied_without_quote(ctx: DealerContext) -> RuleOutcome | None:
    if not ctx.inbound or ctx.current_offer is not None:
        return None
    last = ctx.last_interaction
    assert last is not None
    if last.direction == Direction.OUTBOUND:
        hours = (ctx.now - last.occurred_at).total_seconds() / 3600
        if hours >= _no_response_days(ctx) * 24:
            return RuleOutcome(
                "NO_RESPONSE",
                "WENT_QUIET",
                f"Dealer engaged but has been silent for {hours / 24:.0f} days.",
            )
        return RuleOutcome(
            "AWAITING_QUOTE",
            "QUOTE_REQUESTED",
            "You asked for pricing and are waiting on the dealer.",
        )
    return RuleOutcome(
        "RESPONDED",
        "REPLIED_NO_PRICE",
        "Dealer replied but has not put a price in writing.",
    )


RULES: tuple[Rule, ...] = (
    Rule("no_contact", 10, _rule_no_contact),
    Rule("never_replied", 20, _rule_never_replied),
    Rule("offer_on_table", 30, _rule_offer_on_table),
    Rule("replied_without_quote", 40, _rule_replied_without_quote),
)


def evaluate(ctx: DealerContext) -> RuleOutcome | None:
    """Run the rule set. Terminal states are left alone."""
    if ctx.is_terminal:
        return None
    for rule in sorted(RULES, key=lambda r: r.priority):
        outcome = rule.evaluate(ctx)
        if outcome is not None:
            return outcome
    return None


def rule_id_for(ctx: DealerContext) -> str | None:
    if ctx.is_terminal:
        return None
    for rule in sorted(RULES, key=lambda r: r.priority):
        if rule.evaluate(ctx) is not None:
            return rule.id
    return None


def apply(db: Session, ctx: DealerContext) -> StateTransition | None:
    """Evaluate and persist. Returns the transition row if one was written."""
    outcome = evaluate(ctx)
    if outcome is None:
        return None

    dealer = ctx.dealer
    if outcome.state == dealer.state_code:
        return None

    transition = StateTransition(
        dealer_id=dealer.id,
        from_state=dealer.state_code,
        to_state=outcome.state,
        reason_code=outcome.reason_code,
        reason_text=outcome.reason_text,
        triggered_by="RULE",
        rule_id=rule_id_for(ctx),
        interaction_id=ctx.last_interaction.id if ctx.last_interaction else None,
        was_suppressed_by_pin=dealer.state_is_pinned,
    )
    db.add(transition)

    if not dealer.state_is_pinned:
        dealer.state_code = outcome.state
        dealer.state_changed_at = utcnow()

    db.flush()
    return transition


def set_state_manually(
    db: Session, dealer, to_state: str, *, reason: str | None = None, pin: bool = True
) -> StateTransition:
    """User override. Pinning stops the engine from moving the dealer again."""
    transition = StateTransition(
        dealer_id=dealer.id,
        from_state=dealer.state_code,
        to_state=to_state,
        reason_code="USER_SET",
        reason_text=reason or "Set manually.",
        triggered_by="USER",
    )
    dealer.state_code = to_state
    dealer.state_is_pinned = pin
    dealer.state_changed_at = utcnow()
    db.add(transition)
    db.flush()
    return transition


def refresh_all(db: Session, *, now: datetime | None = None) -> list[StateTransition]:
    """Re-evaluate every dealer. Cheap, and safe to call after any ingest.

    ``now`` is injectable so replay can evaluate each event at the time it actually
    happened. Without it every historical message would be judged against wall clock
    and the whole corpus would read as months overdue.
    """
    transitions = []
    for ctx in build_contexts(db, now=now).values():
        t = apply(db, ctx)
        if t is not None:
            transitions.append(t)
    return transitions


def refresh_one(
    db: Session, dealer_id: int, *, now: datetime | None = None
) -> StateTransition | None:
    return apply(db, build_context(db, dealer_id, now=now))
