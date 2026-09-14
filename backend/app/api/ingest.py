"""Ingestion, sync, replay and outbound endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DbSession, get_dealer_or_404
from app.config import get_settings
from app.enums import Direction
from app.ingestion import eml
from app.ingestion.base import (
    EmlDirectorySource,
    SendRefused,
    get_source,
    get_transport,
)
from app.ingestion.gmail.auth import GmailUnavailable
from app.ingestion.replay import replay as run_replay
from app.models import Contact, DraftMessage, Interaction, SyncState
from app.schemas.entities import InteractionOut
from app.services import outbound as outbound_service
from app.services import sync as sync_service

router = APIRouter(prefix="/api/ingest", tags=["ingestion"])

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "golden"


class SyncRequest(BaseModel):
    mode: str = Field(default="incremental", pattern="^(incremental|historical)$")
    limit: int | None = None


class ReplayRequest(BaseModel):
    directory: str | None = None
    use_golden: bool = False
    seed_dealers: bool = True


class AssignRequest(BaseModel):
    dealer_id: int
    contact_id: int | None = None
    re_extract: bool = True


@router.get("/status")
def status(db: DbSession):
    """What mode this instance is in and where each source got to."""
    settings = get_settings()
    transport = get_transport(settings)
    rows = db.scalars(select(SyncState)).all()
    # Two different things, and conflating them hides the one that needs a human:
    # an unattributed message cannot be filed at all, while a flagged one is filed
    # but has something odd about it (an unexplained charge, a low-confidence read).
    flagged = db.scalars(
        select(Interaction).where(Interaction.needs_review.is_(True))
    ).all()
    unattributed = [row for row in flagged if row.dealer_id is None]
    return {
        "ingest_mode": settings.ingest_mode.upper(),
        "transport": transport.name,
        "send_enabled": settings.send_enabled,
        "send_safe_mode": settings.send_safe_mode,
        "send_allowlist": settings.send_allowlist,
        "golden_corpus_available": GOLDEN_DIR.is_dir() and any(GOLDEN_DIR.glob("*.eml")),
        "needs_review": len(flagged),
        "unattributed": len(unattributed),
        "sync": [
            {
                "provider": row.provider,
                "account": row.account_email,
                "cursor": row.last_history_id,
                "status": row.status,
                "error": row.error,
                "last_full_sync_at": row.last_full_sync_at,
                "last_incremental_at": row.last_incremental_at,
            }
            for row in rows
        ],
    }


@router.post("/sync")
def sync(payload: SyncRequest, db: DbSession):
    """Pull from the configured source. Safe to run repeatedly — duplicates are dropped."""
    settings = get_settings()
    try:
        source = get_source(settings)
        report = sync_service.run(
            db,
            source,
            mode=payload.mode,
            account=settings.gmail_account,
            limit=payload.limit,
        )
    except GmailUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return report.as_dict()


@router.post("/replay")
def replay(payload: ReplayRequest, db: DbSession):
    """Replay a corpus of .eml files chronologically, snapshotting after each message.

    This is the mechanism the golden tests use; exposing it makes it possible to drop a
    sanitized export into a directory and watch the negotiation rebuild itself.
    """
    if payload.use_golden:
        directory = GOLDEN_DIR
    elif payload.directory:
        directory = Path(payload.directory)
    else:
        directory = get_settings().data_dir / "replay"

    if not directory.is_dir():
        raise HTTPException(status_code=404, detail=f"No such directory: {directory}")

    if payload.seed_dealers:
        from app.fixtures import golden_corpus

        if payload.use_golden and not db.scalars(select(Contact).limit(1)).first():
            golden_corpus.seed_profile(db)
            golden_corpus.seed_dealers(db)
            db.flush()

    messages = eml.load_directory(directory, source_system="replay")
    if not messages:
        raise HTTPException(status_code=400, detail=f"No .eml files in {directory}")

    run = run_replay(db, messages)
    final = run.final
    return {
        "messages": len(messages),
        "checkpoints": len(run.checkpoints),
        "timeline": run.timeline(),
        "final": {
            "best_otd_dealer": final.best_otd_dealer,
            "best_otd_cents": final.best_otd_cents,
            "open_contradictions": final.open_contradictions,
            "dealers": {
                name: {
                    "state": snap.state,
                    "otd_cents": snap.otd_cents,
                    "dealer_controlled_cents": snap.dealer_controlled_cents,
                    "offers": snap.offer_count,
                    "owes_response": snap.owes_response,
                    "friction": snap.friction_codes,
                }
                for name, snap in sorted(final.dealers.items())
            },
        },
    }


@router.get("/review", response_model=list[InteractionOut])
def review_queue(db: DbSession):
    """Messages the resolver would not guess about."""
    return db.scalars(
        select(Interaction)
        .where(Interaction.needs_review.is_(True))
        .order_by(Interaction.occurred_at.desc())
    ).all()


@router.post("/review/{interaction_id}/assign", response_model=InteractionOut)
def assign(interaction_id: int, payload: AssignRequest, db: DbSession):
    """Attach an unresolved message to a dealer by hand, and extract from it."""
    interaction = db.get(Interaction, interaction_id)
    if interaction is None:
        raise HTTPException(status_code=404, detail="interaction not found")
    dealer = get_dealer_or_404(db, payload.dealer_id)

    interaction.dealer_id = dealer.id
    interaction.contact_id = payload.contact_id
    interaction.needs_review = False
    db.flush()

    if payload.re_extract and interaction.direction == Direction.INBOUND:
        from app.enrichment import candidates, extract_rules
        from app.services.context import get_profile

        candidate = extract_rules.extract(interaction.normalized_content or "")
        interaction.is_quote_bearing = candidate.has_pricing
        candidates.commit(
            db,
            dealer=dealer,
            candidate=candidate,
            interaction=interaction,
            profile=get_profile(db),
        )

    from app.services import state_engine

    state_engine.refresh_one(db, dealer.id)
    db.flush()
    return interaction


@router.post("/gmail/preview")
def gmail_preview(db: DbSession, limit: int = 25):  # noqa: ARG001 - session keeps deps uniform
    """Headers-only dry run of what a historical import would pull in."""
    settings = get_settings()
    if settings.ingest_mode.upper() != "GMAIL":
        raise HTTPException(
            status_code=400,
            detail=f"Not in GMAIL mode (currently {settings.ingest_mode}).",
        )
    try:
        from app.ingestion.gmail.source import GmailSource

        return {
            "query": settings.gmail_import_query,
            "messages": GmailSource(settings).preview(limit=limit),
        }
    except GmailUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/drafts/{draft_id}/send")
def send_draft(draft_id: int, db: DbSession):
    """Send an approved draft through the configured transport.

    Blocked unless sending is enabled, the recipient is allowlisted, and the draft has
    been explicitly approved. The refusal reason is returned verbatim.
    """
    draft = db.get(DraftMessage, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="draft not found")
    try:
        outcome = outbound_service.send_draft(db, draft)
    except SendRefused as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except GmailUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "draft_id": outcome.draft.id,
        "status": outcome.draft.status,
        "interaction_id": outcome.interaction.id,
        "provider_message_id": outcome.provider_message_id,
        "provider_thread_id": outcome.provider_thread_id,
        "sent_at": outcome.draft.sent_at,
    }


@router.post("/replay/from-upload")
async def replay_from_upload(db: DbSession, directory: str):
    """Replay a directory that already exists on disk (no upload of raw mail over HTTP).

    Deliberately takes a path rather than a file body: the corpus is personal
    correspondence and belongs on the local filesystem, not in an HTTP request.
    """
    return replay(ReplayRequest(directory=directory, seed_dealers=False), db)


__all__ = ["router", "EmlDirectorySource"]
