"""Running a source through the ingestion pipeline and recording where it got to.

The cursor lives in ``sync_state`` so an interrupted sync resumes rather than restarts,
and so the second sync of a session fetches only what is new. Duplicate suppression is
the pipeline's job, not this module's — which is why an over-fetch (an expired Gmail
history cursor falling back to a date-window scan) is safe rather than destructive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.base import MessageSource
from app.ingestion.pipeline import IngestResult, ingest_many
from app.models import SyncState
from app.models.base import utcnow
from app.services import contradictions, notifications, signals, state_engine


@dataclass
class SyncReport:
    provider: str
    mode: str
    fetched: int = 0
    created: int = 0
    duplicates: int = 0
    unresolved: int = 0
    offers: int = 0
    cursor: str | None = None
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None
    results: list[IngestResult] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "mode": self.mode,
            "fetched": self.fetched,
            "created": self.created,
            "duplicates": self.duplicates,
            "unresolved": self.unresolved,
            "offers": self.offers,
            "cursor": self.cursor,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


def get_state(db: Session, provider: str, account: str | None = None) -> SyncState:
    row = db.scalars(
        select(SyncState).where(
            SyncState.provider == provider, SyncState.account_email == account
        )
    ).first()
    if row is None:
        row = SyncState(provider=provider, account_email=account)
        db.add(row)
        db.flush()
    return row


def run(
    db: Session,
    source: MessageSource,
    *,
    mode: str = "incremental",
    account: str | None = None,
    limit: int | None = None,
    refresh_derived: bool = True,
) -> SyncReport:
    """Fetch, ingest, and run the deterministic passes."""
    state = get_state(db, source.name, account)
    report = SyncReport(provider=source.name, mode=mode)
    state.status = "RUNNING"
    db.flush()

    try:
        if mode == "historical" or not state.last_history_id:
            messages = source.fetch_all(limit=limit)
            cursor = state.last_history_id
            sync = getattr(source, "fetch_incremental", None)
            if sync is not None and messages:
                # Take a cursor forward even on a full import so the next run is
                # incremental rather than a second full pull.
                cursor = sync(None).cursor
            report.mode = "historical"
        else:
            result = source.fetch_incremental(state.last_history_id)
            messages, cursor = result.messages, result.cursor

        report.fetched = len(messages)
        results = ingest_many(db, messages, refresh_state=False)
        report.results = results
        report.created = sum(1 for r in results if r.created)
        report.duplicates = sum(1 for r in results if r.duplicate)
        report.unresolved = sum(1 for r in results if r.created and r.dealer_id is None)
        report.offers = sum(1 for r in results if r.offer is not None)
        report.cursor = cursor

        state.last_history_id = cursor
        state.status = "IDLE"
        state.error = None
        if report.mode == "historical":
            state.last_full_sync_at = utcnow()
        state.last_incremental_at = utcnow()
    except Exception as exc:
        state.status = "ERROR"
        # Message only; a traceback from the Google client can carry request URLs
        # with tokens in them.
        state.error = f"{exc.__class__.__name__}: {exc}"
        db.flush()
        raise
    finally:
        report.finished_at = utcnow()
        db.flush()

    if refresh_derived:
        state_engine.refresh_all(db)
        signals.refresh_signals(db)
        contradictions.detect_all(db)
        notifications.refresh(db)
        db.flush()

    return report
