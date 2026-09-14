"""Seed data for the negotiation state catalogue.

These are defaults, not a closed set — states live in a table so the workflow can be
extended with an INSERT rather than a migration.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NegotiationStateDef


@dataclass(frozen=True)
class StateSeed:
    code: str
    label: str
    description: str
    sort_order: int
    is_terminal: bool = False
    is_active_pipeline: bool = True


DEFAULT_STATES: tuple[StateSeed, ...] = (
    StateSeed("DISCOVERED", "Discovered", "Identified but not yet contacted.", 10),
    StateSeed("CONTACTED", "Contacted", "Initial inquiry sent, reply not yet due.", 20),
    StateSeed(
        "AWAITING_RESPONSE", "Awaiting response", "Inquiry sent, dealer has not replied.", 30
    ),
    StateSeed("RESPONDED", "Responded", "Dealer replied but has not quoted a price.", 40),
    StateSeed("AWAITING_QUOTE", "Awaiting quote", "A quote has been requested.", 50),
    StateSeed("QUOTE_RECEIVED", "Quote received", "A priced offer is on the table.", 60),
    StateSeed("COUNTER_SENT", "Counter sent", "You countered; reply not yet due.", 70),
    StateSeed("AWAITING_COUNTER", "Awaiting counter", "You countered and are waiting.", 80),
    StateSeed("FINALIST", "Finalist", "In contention to win the deal.", 90),
    StateSeed("ACCEPTED", "Accepted", "Deal agreed.", 100, is_terminal=True,
              is_active_pipeline=False),
    StateSeed("LOST", "Lost", "Bought elsewhere or dealer withdrew.", 110, is_terminal=True,
              is_active_pipeline=False),
    StateSeed("CLOSED", "Closed", "Deliberately closed out.", 120, is_terminal=True,
              is_active_pipeline=False),
    StateSeed("NO_RESPONSE", "No response", "Contacted repeatedly with no reply.", 130,
              is_active_pipeline=False),
)


def ensure_states(db: Session) -> None:
    """Idempotently seed the catalogue. Safe to call on every startup."""
    existing = {s.code for s in db.scalars(select(NegotiationStateDef)).all()}
    for seed in DEFAULT_STATES:
        if seed.code in existing:
            continue
        db.add(
            NegotiationStateDef(
                code=seed.code,
                label=seed.label,
                description=seed.description,
                sort_order=seed.sort_order,
                is_terminal=seed.is_terminal,
                is_active_pipeline=seed.is_active_pipeline,
                is_builtin=True,
            )
        )
    db.flush()
