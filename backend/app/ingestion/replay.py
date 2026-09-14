"""Chronological replay of a historical negotiation.

Feeds messages into the ingestion pipeline one at a time, in the order they were
actually sent, and snapshots the whole board after each one. That turns "what did the
system believe at T4?" into an assertion rather than an argument, and it is the only
honest way to test a state engine: the interesting bugs are ordering bugs, and a test
that loads everything at once cannot see them.

Every event is evaluated at its own timestamp, so idle times, follow-up thresholds and
deadlines behave as they did on the day rather than relative to whenever the test runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from app.enums import BehaviorDimension
from app.ingestion.messages import RawMessage
from app.ingestion.pipeline import IngestResult, ingest
from app.services import contradictions, dashboard, notifications, signals
from app.services.context import build_contexts, get_profile


@dataclass
class DealerSnapshot:
    name: str
    state: str
    owes_response: str
    otd_cents: int | None
    dealer_controlled_cents: int | None
    offer_version: int | None
    offer_count: int
    # Only FRICTION-dimension signals. Mixing the positive ones in would make
    # "does Tarrytown have friction?" answer yes for a dealer who did everything right.
    friction_codes: list[str] = field(default_factory=list)
    signal_codes: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    next_action: str | None = None

    def has_friction(self, code: str) -> bool:
        return code in self.friction_codes


@dataclass
class Checkpoint:
    """The state of the whole negotiation immediately after one message."""

    index: int
    at: datetime
    message_id: str
    subject: str | None
    direction: str
    dealer: str | None
    ingested: bool
    duplicate: bool
    offer_recorded: bool
    dealers: dict[str, DealerSnapshot] = field(default_factory=dict)
    best_otd_dealer: str | None = None
    best_otd_cents: int | None = None
    best_dealer_controlled_dealer: str | None = None
    best_dealer_controlled_cents: int | None = None
    open_contradictions: int = 0
    notifications: int = 0

    @property
    def label(self) -> str:
        return f"T{self.index}"

    def __getitem__(self, dealer_name: str) -> DealerSnapshot:
        return self.dealers[dealer_name]

    def state_of(self, dealer_name: str) -> str:
        return self.dealers[dealer_name].state


@dataclass
class ReplayRun:
    checkpoints: list[Checkpoint] = field(default_factory=list)
    results: list[IngestResult] = field(default_factory=list)

    def at(self, index: int) -> Checkpoint:
        """Checkpoint by index — ``run.at(4)`` is the board right after T4."""
        return self.checkpoints[index]

    @property
    def final(self) -> Checkpoint:
        return self.checkpoints[-1]

    def timeline(self) -> list[str]:
        """One line per event. Printed by the CLI, and useful in test failures."""
        lines = []
        for point in self.checkpoints:
            arrow = "→" if point.direction == "OUTBOUND" else "←"
            lines.append(
                f"{point.label:>4}  {point.at:%b %d %H:%M}  {arrow} "
                f"{(point.dealer or 'unresolved'):<22} "
                f"{(point.subject or '')[:44]:<46}"
                + (
                    f"best={point.best_otd_dealer} "
                    f"{(point.best_otd_cents or 0) / 100:,.2f}"
                    if point.best_otd_cents
                    else ""
                )
            )
        return lines


def _snapshot(
    db: Session, *, index: int, message: RawMessage, result: IngestResult
) -> Checkpoint:
    now = message.sent_at
    view = dashboard.build(db, now=now)
    contexts = build_contexts(db, now=now)

    dealers: dict[str, DealerSnapshot] = {}
    for row in view.rows:
        ctx = contexts.get(row.dealer_id)
        dealers[row.dealer_name] = DealerSnapshot(
            name=row.dealer_name,
            state=row.state_code,
            owes_response=row.owes_response,
            otd_cents=row.otd_cents,
            dealer_controlled_cents=row.dealer_controlled_cents,
            offer_version=row.offer_version,
            offer_count=len(ctx.offers) if ctx else 0,
            friction_codes=sorted(
                {s.code for s in ctx.signals if s.dimension == BehaviorDimension.FRICTION}
            )
            if ctx
            else [],
            signal_codes=sorted({s.code for s in ctx.signals}) if ctx else [],
            unresolved=list(row.unresolved_issues),
            next_action=row.next_action,
        )

    return Checkpoint(
        index=index,
        at=now,
        message_id=message.source_identifier,
        subject=message.subject,
        direction=result.direction or "UNKNOWN",
        dealer=next(
            (c.dealer.name for c in contexts.values() if c.dealer.id == result.dealer_id),
            None,
        ),
        ingested=result.created,
        duplicate=result.duplicate,
        offer_recorded=result.offer is not None,
        dealers=dealers,
        best_otd_dealer=view.summary.best_otd_dealer,
        best_otd_cents=view.summary.best_otd_cents,
        best_dealer_controlled_dealer=view.summary.best_dealer_controlled_dealer,
        best_dealer_controlled_cents=view.summary.best_dealer_controlled_cents,
        open_contradictions=view.summary.open_contradictions,
        notifications=len(notifications.unread(db, limit=500)),
    )


def replay(
    db: Session,
    messages: list[RawMessage],
    *,
    snapshot_every_event: bool = True,
) -> ReplayRun:
    """Replay a corpus chronologically, snapshotting the board after each message."""
    profile = get_profile(db)
    ordered = sorted(messages, key=lambda m: (m.sent_at, m.source_identifier))
    run = ReplayRun()

    for index, message in enumerate(ordered):
        result = ingest(db, message, profile=profile, now=message.sent_at)
        run.results.append(result)

        # The derived passes run at the event's own time, so a checkpoint reflects
        # what the dashboard would have shown that afternoon.
        signals.refresh_signals(db, now=message.sent_at)
        contradictions.detect_all(db, now=message.sent_at)
        notifications.refresh(db, now=message.sent_at)
        db.flush()

        if snapshot_every_event or index == len(ordered) - 1:
            run.checkpoints.append(
                _snapshot(db, index=index, message=message, result=result)
            )

    return run
