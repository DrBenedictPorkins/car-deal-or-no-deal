"""The dashboard projection.

Built to answer, in one screen and under ten seconds:

  1. Who have I contacted?  2. Who responded?  3. Who hasn't?  4. Best offer?
  5. What changed recently?  6. Who owes the next response?  7. What next?

Rows are ordered by the urgency of the recommended action, not alphabetically, so the
top of the table is always the work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import Party
from app.models import Dealer, Fact, Offer, StateTransition
from app.models.base import utcnow
from app.services import behavior, comparison, recommender
from app.services.context import DealerContext, build_contexts
from app.services.money import fmt
from app.services.pricing import progression


@dataclass
class DashboardRow:
    dealer_id: int
    dealer_name: str
    is_local: bool
    distance_miles: float | None

    primary_contact: str | None
    primary_contact_role: str | None
    contact_is_automated: bool

    vehicle_summary: str | None
    vin: str | None

    selling_price_cents: int | None
    dealer_controlled_cents: int | None
    otd_cents: int | None
    otd_is_written: bool
    otd_variance_cents: int | None
    offer_version: int | None

    state_code: str
    state_label: str
    state_is_pinned: bool

    last_interaction_at: str | None
    last_interaction_channel: str | None
    last_interaction_direction: str | None
    idle_hours: float | None

    owes_response: str
    owes_reason: str

    next_action_code: str
    next_action: str
    next_action_detail: str
    next_action_priority: int
    next_action_severity: str

    unresolved_issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    friction_points: float = 0.0
    friction_reasons: list[str] = field(default_factory=list)

    is_best_otd: bool = False
    gap_to_best_cents: int | None = None


@dataclass
class DashboardSummary:
    dealers_total: int
    dealers_contacted: int
    dealers_responded: int
    dealers_silent: int
    dealers_with_offer: int
    best_otd_cents: int | None
    best_otd_dealer: str | None
    best_dealer_controlled_cents: int | None
    best_dealer_controlled_dealer: str | None
    you_owe_count: int
    dealer_owes_count: int
    open_contradictions: int
    recent_changes: list[str] = field(default_factory=list)


@dataclass
class Dashboard:
    summary: DashboardSummary
    rows: list[DashboardRow]


def _primary_contact(ctx: DealerContext):
    contacts = ctx.dealer.contacts
    if not contacts:
        return None, None, False
    primary = next((c for c in contacts if c.is_primary), None)
    if primary is None:
        # Fall back to whoever actually wrote to us most recently.
        by_id = {c.id: c for c in contacts}
        for interaction in sorted(
            ctx.inbound, key=lambda i: i.occurred_at, reverse=True
        ):
            if interaction.contact_id in by_id:
                primary = by_id[interaction.contact_id]
                break
    primary = primary or contacts[0]
    return primary.name, primary.role, primary.actor_kind == "AUTOMATED"


def recent_changes(
    db: Session, *, since_hours: int = 24, limit: int = 12, now: datetime | None = None
) -> list[str]:
    """"What changed since yesterday?" answered from the append-only tables."""
    cutoff = (now or utcnow()) - timedelta(hours=since_hours)
    names = {d.id: d.name for d in db.scalars(select(Dealer)).all()}

    def who(dealer_id: int | None) -> str:
        return names.get(dealer_id, f"Dealer #{dealer_id}")

    lines: list[str] = []

    for offer in db.scalars(
        select(Offer).where(Offer.created_at >= cutoff).order_by(Offer.created_at.desc())
    ).all():
        lines.append(
            f"{who(offer.dealer_id)}: offer v{offer.version} recorded"
            + (f" at {fmt(offer.quoted_otd_cents)} OTD" if offer.quoted_otd_cents else "")
        )

    for transition in db.scalars(
        select(StateTransition)
        .where(StateTransition.created_at >= cutoff)
        .order_by(StateTransition.created_at.desc())
    ).all():
        if transition.was_suppressed_by_pin:
            continue
        lines.append(
            f"{who(transition.dealer_id)}: {transition.from_state} → "
            f"{transition.to_state} ({transition.reason_text})"
        )

    for fact in db.scalars(
        select(Fact).where(Fact.created_at >= cutoff).order_by(Fact.created_at.desc())
    ).all():
        lines.append(
            f"{who(fact.dealer_id)}: {fact.attribute} = {fact.display_value}"
        )

    return lines[:limit]


def build(db: Session, *, now: datetime | None = None) -> Dashboard:
    contexts = list(build_contexts(db, now=now).values())
    rows_by_dealer = {r.dealer_id: r for r in comparison.build_rows(contexts)}

    priced = [
        c
        for c in contexts
        if c.current_pricing and c.current_pricing.effective_otd_cents is not None
    ]
    best_otd = (
        min((c.current_pricing.effective_otd_cents, c.dealer.name) for c in priced)
        if priced
        else None
    )
    by_cost = [
        (c.current_pricing.dealer_controlled_cents, c.dealer.name)
        for c in contexts
        if c.current_pricing and c.current_pricing.dealer_controlled_cents is not None
    ]
    best_cost = min(by_cost) if by_cost else None

    accepted_with = recommender.accepted_dealer(contexts)

    rows: list[DashboardRow] = []
    you_owe = dealer_owes = 0
    contradictions = 0

    for ctx in contexts:
        p = ctx.current_pricing
        offer = ctx.current_offer
        state_def = ctx.state_defs.get(ctx.dealer.state_code)
        owes, owes_reason = ctx.owes_response()
        if owes == Party.BUYER:
            you_owe += 1
        elif owes == Party.DEALER:
            dealer_owes += 1
        contradictions += len(ctx.open_contradictions)

        benchmark = recommender.best_benchmark(contexts, excluding_dealer_id=ctx.dealer.id)
        rec = recommender.recommend(ctx, benchmark, deal_accepted_with=accepted_with)
        report = behavior.report(ctx)
        name, role, automated = _primary_contact(ctx)
        comparison_row = rows_by_dealer.get(ctx.dealer.id)
        last = ctx.last_interaction

        otd = p.effective_otd_cents if p else None
        gap = (
            otd - best_otd[0]
            if otd is not None and best_otd is not None
            else None
        )

        rows.append(
            DashboardRow(
                dealer_id=ctx.dealer.id,
                dealer_name=ctx.dealer.name,
                is_local=ctx.dealer.is_local,
                distance_miles=float(ctx.dealer.distance_miles)
                if ctx.dealer.distance_miles is not None
                else None,
                primary_contact=name,
                primary_contact_role=role,
                contact_is_automated=automated,
                vehicle_summary=comparison_row.vehicle_summary if comparison_row else None,
                vin=comparison_row.vin if comparison_row else None,
                selling_price_cents=p.selling_price_cents if p else None,
                dealer_controlled_cents=p.dealer_controlled_cents if p else None,
                otd_cents=otd,
                otd_is_written=bool(p and p.quoted_otd_cents is not None),
                otd_variance_cents=p.otd_variance_cents if p else None,
                offer_version=offer.version if offer else None,
                state_code=ctx.dealer.state_code,
                state_label=state_def.label if state_def else ctx.dealer.state_code,
                state_is_pinned=ctx.dealer.state_is_pinned,
                last_interaction_at=last.occurred_at.isoformat() if last else None,
                last_interaction_channel=str(last.channel) if last else None,
                last_interaction_direction=str(last.direction) if last else None,
                idle_hours=ctx.idle_hours,
                owes_response=str(owes),
                owes_reason=owes_reason,
                next_action_code=rec.code,
                next_action=rec.headline,
                next_action_detail=rec.detail,
                next_action_priority=rec.priority,
                next_action_severity=rec.severity,
                unresolved_issues=comparison_row.unresolved_issues if comparison_row else [],
                warnings=p.warnings if p else [],
                friction_points=report.friction_points,
                friction_reasons=report.friction_reasons,
                is_best_otd=bool(
                    best_otd is not None and otd is not None and otd == best_otd[0]
                ),
                gap_to_best_cents=gap,
            )
        )

    rows.sort(key=lambda r: (r.next_action_priority, r.otd_cents or 10**12, r.dealer_name))

    summary = DashboardSummary(
        dealers_total=len(contexts),
        dealers_contacted=sum(1 for c in contexts if c.outbound),
        dealers_responded=sum(1 for c in contexts if c.inbound),
        dealers_silent=sum(1 for c in contexts if c.outbound and not c.inbound),
        dealers_with_offer=len(priced),
        best_otd_cents=best_otd[0] if best_otd else None,
        best_otd_dealer=best_otd[1] if best_otd else None,
        best_dealer_controlled_cents=best_cost[0] if best_cost else None,
        best_dealer_controlled_dealer=best_cost[1] if best_cost else None,
        you_owe_count=you_owe,
        dealer_owes_count=dealer_owes,
        open_contradictions=contradictions,
        recent_changes=recent_changes(db, now=now),
    )
    return Dashboard(summary=summary, rows=rows)


def timeline(ctx: DealerContext) -> list[dict]:
    """Chronological merge of every channel, plus offers as timeline events."""
    events: list[dict] = []
    for interaction in ctx.interactions:
        events.append(
            {
                "kind": "INTERACTION",
                "id": interaction.id,
                "at": interaction.occurred_at.isoformat(),
                "channel": str(interaction.channel),
                "direction": str(interaction.direction),
                "actor_kind": str(interaction.actor_kind),
                "subject": interaction.subject,
                "preview": (interaction.normalized_content or "")[:400],
                "is_quote_bearing": interaction.is_quote_bearing,
                "needs_review": interaction.needs_review,
            }
        )
    for result in ctx.offer_history:
        events.append(
            {
                "kind": "OFFER",
                "id": result.offer_id,
                "at": result.quoted_at.isoformat(),
                "version": result.version,
                "otd_cents": result.effective_otd_cents,
                "dealer_controlled_cents": result.dealer_controlled_cents,
                "subject": f"Offer v{result.version}",
                "warnings": result.warnings,
            }
        )
    events.sort(key=lambda e: e["at"])
    return events


def offer_progression(ctx: DealerContext) -> list[dict]:
    return [
        {
            "from_version": d.from_version,
            "to_version": d.to_version,
            "selling_price_delta_cents": d.selling_price_delta_cents,
            "dealer_controlled_delta_cents": d.dealer_controlled_delta_cents,
            "otd_delta_cents": d.otd_delta_cents,
            "add_ons_delta_cents": d.add_ons_delta_cents,
            "added_lines": list(d.added_line_names),
            "removed_lines": list(d.removed_line_names),
            "improved": d.improved,
        }
        for d in progression(ctx.offer_history)
    ]


__all__ = [
    "Dashboard",
    "DashboardRow",
    "DashboardSummary",
    "build",
    "offer_progression",
    "recent_changes",
    "timeline",
]
