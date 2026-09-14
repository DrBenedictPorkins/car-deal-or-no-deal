"""Notification emission.

"Do not over-notify" is implemented as a uniqueness constraint rather than a good
intention: every notification carries a ``dedupe_key`` derived from the thing that
happened, so re-running the refresh pass cannot produce a second copy.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import NotificationType, Severity
from app.models import Notification
from app.services import recommender
from app.services.context import build_contexts
from app.services.money import fmt


def emit(
    db: Session,
    *,
    type_: NotificationType | str,
    title: str,
    dedupe_key: str,
    body: str | None = None,
    severity: Severity | str = Severity.INFO,
    dealer_id: int | None = None,
    interaction_id: int | None = None,
    offer_id: int | None = None,
    context: dict | None = None,
) -> Notification | None:
    """Returns the new row, or None if this notification already exists."""
    existing = db.scalars(
        select(Notification).where(Notification.dedupe_key == dedupe_key)
    ).first()
    if existing is not None:
        return None
    row = Notification(
        dealer_id=dealer_id,
        type=str(type_),
        severity=str(severity),
        title=title,
        body=body,
        dedupe_key=dedupe_key,
        context_json=json.dumps(context) if context else None,
        interaction_id=interaction_id,
        offer_id=offer_id,
    )
    db.add(row)
    db.flush()
    return row


def refresh(db: Session) -> list[Notification]:
    """Recompute notifications from current state. Safe to run repeatedly."""
    contexts = list(build_contexts(db).values())
    created: list[Notification] = []

    priced = [
        c
        for c in contexts
        if c.current_pricing and c.current_pricing.effective_otd_cents is not None
    ]
    best_otd = (
        min(c.current_pricing.effective_otd_cents for c in priced) if priced else None
    )

    for ctx in contexts:
        dealer = ctx.dealer
        p = ctx.current_pricing

        # --- offers ------------------------------------------------------
        for result in ctx.offer_history:
            row = emit(
                db,
                type_=NotificationType.NEW_OFFER,
                severity=Severity.INFO,
                title=f"{dealer.name}: offer v{result.version} "
                f"at {fmt(result.effective_otd_cents)} OTD",
                body=f"Dealer-controlled cost {fmt(result.dealer_controlled_cents)}.",
                dedupe_key=f"offer:{result.offer_id}",
                dealer_id=dealer.id,
                offer_id=result.offer_id,
            )
            if row:
                created.append(row)

        if p is not None:
            if (
                best_otd is not None
                and p.effective_otd_cents == best_otd
                and len(priced) > 1
            ):
                row = emit(
                    db,
                    type_=NotificationType.NEW_BEST_OFFER,
                    severity=Severity.INFO,
                    title=f"{dealer.name} now has the best offer: "
                    f"{fmt(p.effective_otd_cents)} OTD",
                    dedupe_key=f"best:{p.offer_id}",
                    dealer_id=dealer.id,
                    offer_id=p.offer_id,
                )
                if row:
                    created.append(row)

            if p.otd_variance_cents:
                row = emit(
                    db,
                    type_=NotificationType.NEEDS_REVIEW,
                    severity=Severity.WARNING,
                    title=f"{dealer.name}: {fmt(abs(p.otd_variance_cents))} unexplained "
                    f"in the quote",
                    body=p.warnings[0] if p.warnings else None,
                    dedupe_key=f"variance:{p.offer_id}",
                    dealer_id=dealer.id,
                    offer_id=p.offer_id,
                )
                if row:
                    created.append(row)

            for line in p.lines:
                if line.kind == "ADD_ON" and not line.user_wants:
                    row = emit(
                        db,
                        type_=NotificationType.NEW_ADD_ON,
                        severity=Severity.WARNING,
                        title=f"{dealer.name} added {line.name} ({fmt(line.price_cents)})",
                        body="You said you don't want dealer add-ons.",
                        dedupe_key=f"addon:{line.id}",
                        dealer_id=dealer.id,
                        offer_id=p.offer_id,
                    )
                    if row:
                        created.append(row)

        # --- price movement ------------------------------------------------
        from app.services.pricing import progression

        for delta in progression(ctx.offer_history):
            if delta.otd_delta_cents:
                direction = "dropped" if delta.otd_delta_cents < 0 else "rose"
                row = emit(
                    db,
                    type_=NotificationType.PRICE_CHANGED,
                    severity=Severity.INFO
                    if delta.otd_delta_cents < 0
                    else Severity.WARNING,
                    title=f"{dealer.name} {direction} "
                    f"{fmt(abs(delta.otd_delta_cents))} "
                    f"(v{delta.from_version} → v{delta.to_version})",
                    dedupe_key=f"delta:{dealer.id}:{delta.from_version}:{delta.to_version}",
                    dealer_id=dealer.id,
                )
                if row:
                    created.append(row)

        # --- silence --------------------------------------------------------
        owes, _ = ctx.owes_response()
        idle_hours = ctx.idle_hours
        if owes == "DEALER" and idle_hours is not None and idle_hours >= 24:
            bucket = int(idle_hours // 24)
            row = emit(
                db,
                type_=NotificationType.NO_RESPONSE_24H,
                severity=Severity.WARNING if bucket >= 2 else Severity.INFO,
                title=f"{dealer.name} hasn't replied in {bucket} day(s)",
                dedupe_key=f"silence:{dealer.id}:{bucket}",
                dealer_id=dealer.id,
            )
            if row:
                created.append(row)

        # --- deadlines ------------------------------------------------------
        offer = ctx.current_offer
        if offer is not None and offer.expires_at is not None:
            hours_left = (offer.expires_at - ctx.now).total_seconds() / 3600
            if 0 <= hours_left <= 48:
                row = emit(
                    db,
                    type_=NotificationType.DEADLINE_APPROACHING,
                    severity=Severity.CRITICAL,
                    title=f"{dealer.name}'s offer expires "
                    f"{offer.expires_at:%b %d at %H:%M}",
                    body=offer.deadline_note,
                    dedupe_key=f"deadline:{offer.id}",
                    dealer_id=dealer.id,
                    offer_id=offer.id,
                )
                if row:
                    created.append(row)

        # --- contradictions --------------------------------------------------
        for contradiction in ctx.open_contradictions:
            row = emit(
                db,
                type_=NotificationType.CONTRADICTION_DETECTED,
                severity=Severity.WARNING,
                title=contradiction.summary,
                body=f"{contradiction.detail_a}\n{contradiction.detail_b}",
                dedupe_key=f"contradiction:{contradiction.id}",
                dealer_id=dealer.id,
            )
            if row:
                created.append(row)

    return created


def unread(db: Session, limit: int = 50) -> list[Notification]:
    return list(
        db.scalars(
            select(Notification)
            .where(Notification.dismissed_at.is_(None))
            .order_by(Notification.created_at.desc())
            .limit(limit)
        ).all()
    )


def best_benchmark_for(db: Session, dealer_id: int):
    contexts = list(build_contexts(db).values())
    return recommender.best_benchmark(contexts, excluding_dealer_id=dealer_id)
