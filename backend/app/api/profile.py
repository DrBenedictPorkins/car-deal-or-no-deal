from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import DbSession
from app.models import BuyerProfile
from app.schemas.entities import BuyerProfileIn, BuyerProfileOut

router = APIRouter(prefix="/api/profile", tags=["profile"])


def _get_or_create(db) -> BuyerProfile:
    profile = db.scalars(select(BuyerProfile).order_by(BuyerProfile.id).limit(1)).first()
    if profile is None:
        profile = BuyerProfile(id=1)
        db.add(profile)
        db.flush()
    return profile


@router.get("", response_model=BuyerProfileOut)
def read_profile(db: DbSession) -> BuyerProfile:
    return _get_or_create(db)


@router.put("", response_model=BuyerProfileOut)
def update_profile(payload: BuyerProfileIn, db: DbSession) -> BuyerProfile:
    profile = _get_or_create(db)
    for key, value in payload.model_dump().items():
        setattr(profile, key, value)
    db.flush()
    return profile
