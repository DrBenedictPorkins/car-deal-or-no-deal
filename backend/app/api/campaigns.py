"""Campaigns — one shopping effort, and the anchor for the inbox sweep."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DbSession
from app.models import Campaign
from app.models.base import utcnow
from app.schemas.common import ORMModel

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])

# Offered in the UI so "sometime last week" is one click rather than a date picker.
QUICK_PICKS = (3, 7, 14, 30)


class CampaignIn(BaseModel):
    name: str
    target_description: str | None = None
    # When the buyer first reached out. The sweep anchor — not a rolling window,
    # because the oldest messages are the opening offers everything else is
    # measured against, and they must never scroll out of scope.
    opened_at: datetime | None = None
    days_ago: int | None = Field(
        default=None,
        description="Alternative to opened_at: 'I started about N days ago'.",
    )
    notes: str | None = None


class CampaignPatch(BaseModel):
    name: str | None = None
    target_description: str | None = None
    opened_at: datetime | None = None
    days_ago: int | None = None
    status: str | None = None
    notes: str | None = None


class CampaignOut(ORMModel):
    id: int
    name: str
    target_description: str | None = None
    status: str
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    notes: str | None = None


def _resolve_start(opened_at: datetime | None, days_ago: int | None) -> datetime | None:
    if opened_at is not None:
        return opened_at
    if days_ago is not None:
        # Midnight, so "7 days ago" means the whole of that day.
        start = utcnow() - timedelta(days=days_ago)
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    return None


@router.get("/quick-picks", response_model=list[int])
def quick_picks():
    return list(QUICK_PICKS)


@router.get("", response_model=list[CampaignOut])
def list_campaigns(db: DbSession):
    return db.scalars(select(Campaign).order_by(Campaign.id.desc())).all()


@router.get("/active", response_model=CampaignOut | None)
def active_campaign(db: DbSession):
    return db.scalars(
        select(Campaign).where(Campaign.status == "ACTIVE").order_by(Campaign.id.desc())
    ).first()


@router.post("", response_model=CampaignOut, status_code=201)
def create_campaign(payload: CampaignIn, db: DbSession):
    data = payload.model_dump(exclude={"days_ago"})
    data["opened_at"] = _resolve_start(payload.opened_at, payload.days_ago) or utcnow()
    campaign = Campaign(**data)
    db.add(campaign)
    db.flush()
    return campaign


@router.patch("/{campaign_id}", response_model=CampaignOut)
def update_campaign(campaign_id: int, payload: CampaignPatch, db: DbSession):
    """Change the start date and the next sweep reaches further back.

    Re-sweeping a range already covered is safe: the inbox dedupes on the
    provider's message id, so widening the window adds only what was missing.
    """
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="campaign not found")
    data = payload.model_dump(exclude_unset=True, exclude={"days_ago"})
    start = _resolve_start(payload.opened_at, payload.days_ago)
    if start is not None:
        data["opened_at"] = start
    for key, value in data.items():
        setattr(campaign, key, value)
    db.flush()
    return campaign
