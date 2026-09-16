"""The inbox: sweep, look, claim.

No tagging in Gmail, no AI sorting a personal mailbox. The buyer sees what
arrived and says which messages are dealerships.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DbSession
from app.config import get_settings
from app.enums import InboxStatus
from app.ingestion.base import get_source
from app.ingestion.gmail.auth import GmailUnavailable
from app.models import Campaign, InboxMessage
from app.models.base import utcnow
from app.schemas.common import ORMModel
from app.services import inbox as inbox_service

router = APIRouter(prefix="/api/inbox", tags=["inbox"])


class InboxMessageOut(ORMModel):
    id: int
    source_system: str
    source_identifier: str
    thread_identifier: str | None = None
    from_name: str | None = None
    from_email: str | None = None
    to_emails: str | None = None
    subject: str | None = None
    snippet: str | None = None
    sent_at: datetime
    has_attachments: bool
    status: str
    dealer_id: int | None = None
    promoted_interaction_id: int | None = None
    score: float
    reasons: list[str] = Field(default_factory=list)
    suggested_dealer_name: str | None = None


class SweepRequest(BaseModel):
    since: datetime | None = None
    days_ago: int | None = None
    limit: int | None = None


class PromoteRequest(BaseModel):
    dealer_id: int | None = Field(
        default=None, description="Fold into this dealership instead of creating one."
    )
    dealer_name: str | None = Field(
        default=None, description="Override the suggested name for a new dealership."
    )
    claim_neighbours: bool = True


def _serialize(row: InboxMessage) -> InboxMessageOut:
    out = InboxMessageOut.model_validate(row)
    out.reasons = row.reasons
    if row.status == InboxStatus.NEW:
        out.suggested_dealer_name = inbox_service.suggest_dealer_name(row)
    return out


@router.get("", response_model=list[InboxMessageOut])
def list_inbox(db: DbSession, status: str = InboxStatus.NEW, limit: int = 200):
    return [_serialize(row) for row in inbox_service.listing(db, status=status, limit=limit)]


@router.get("/counts")
def inbox_counts(db: DbSession):
    settings = get_settings()
    since = inbox_service.default_since(db)
    return {
        **inbox_service.counts(db),
        "sweep_since": since.isoformat() if since else None,
        "ingest_mode": settings.ingest_mode.upper(),
    }


@router.post("/sweep")
def sweep(payload: SweepRequest, db: DbSession):
    """Pull metadata for everything since the campaign started.

    Bodies are not fetched. Re-running is safe — already-seen messages are
    counted, not duplicated — so widening the window is a normal thing to do.
    """
    campaign = db.scalars(
        select(Campaign).where(Campaign.status == "ACTIVE").order_by(Campaign.id.desc())
    ).first()

    since = payload.since
    if since is None and payload.days_ago is not None:
        since = (utcnow() - timedelta(days=payload.days_ago)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    if since is None:
        since = inbox_service.default_since(db, campaign)
    if since is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "No start date. Create a campaign and say when you first reached out, "
                "or pass days_ago — sweeping an unbounded mailbox is never the right "
                "default."
            ),
        )

    try:
        source = get_source()
        report = inbox_service.sweep(
            db, source, since=since, campaign=campaign, limit=payload.limit
        )
    except GmailUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return report.as_dict()


@router.post("/{message_id}/promote")
def promote(message_id: int, payload: PromoteRequest, db: DbSession):
    """Claim a message. Creates a dealership, or folds into one that exists."""
    row = db.get(InboxMessage, message_id)
    if row is None:
        raise HTTPException(status_code=404, detail="message not found")
    if row.status == InboxStatus.PROMOTED:
        return {
            "dealer_id": row.dealer_id,
            "created_dealer": False,
            "already_promoted": True,
            "also_claimed": [],
        }
    try:
        result = inbox_service.promote(
            db,
            row,
            get_source(),
            dealer_id=payload.dealer_id,
            dealer_name=payload.dealer_name,
            claim_neighbours=payload.claim_neighbours,
        )
    except LookupError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except GmailUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "dealer_id": result.dealer.id,
        "dealer_name": result.dealer.name,
        "created_dealer": result.created_dealer,
        "learned_domain": result.learned_domain,
        "interaction_id": result.interaction.id if result.interaction else None,
        "offer_id": result.offer_id,
        "also_claimed": result.also_claimed,
        "already_promoted": False,
    }


@router.post("/{message_id}/ignore", response_model=InboxMessageOut)
def ignore(message_id: int, db: DbSession):
    row = db.get(InboxMessage, message_id)
    if row is None:
        raise HTTPException(status_code=404, detail="message not found")
    return _serialize(inbox_service.ignore(db, row))


@router.post("/{message_id}/restore", response_model=InboxMessageOut)
def restore(message_id: int, db: DbSession):
    row = db.get(InboxMessage, message_id)
    if row is None:
        raise HTTPException(status_code=404, detail="message not found")
    return _serialize(inbox_service.restore(db, row))


@router.post("/rescore")
def rescore(db: DbSession):
    return {"rescored": inbox_service.rescore_all(db)}
