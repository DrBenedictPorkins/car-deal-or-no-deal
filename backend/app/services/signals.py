"""Behavior-signal persistence.

``behavior.derive_signals`` is pure and returns plain dicts; this is the one place
that writes them, so the refresh endpoint, the CLI and the replay engine cannot drift
apart on what "refresh the signals" means.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BehaviorSignal
from app.services import behavior
from app.services.context import build_contexts


def refresh_signals(db: Session, *, now: datetime | None = None) -> int:
    """Derive and store any signal not already recorded. Returns how many were new."""
    existing = {
        key
        for key in db.scalars(select(BehaviorSignal.dedupe_key)).all()
        if key
    }
    added = 0
    for ctx in build_contexts(db, now=now).values():
        for payload in behavior.derive_signals(ctx):
            if payload["dedupe_key"] in existing:
                continue
            existing.add(payload["dedupe_key"])
            db.add(BehaviorSignal(**payload))
            added += 1
    db.flush()
    return added
