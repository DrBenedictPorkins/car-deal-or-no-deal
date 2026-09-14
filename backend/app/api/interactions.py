from __future__ import annotations

import hashlib

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import DbSession
from app.models import Interaction
from app.models.base import utcnow
from app.schemas.entities import InteractionIn, InteractionOut
from app.services import state_engine

router = APIRouter(prefix="/api/interactions", tags=["interactions"])


def content_hash(payload: InteractionIn) -> str:
    """Stable identity for an interaction's content.

    Includes the dealer, channel, direction and timestamp so that two dealers sending
    the same boilerplate are not treated as duplicates of one another.
    """
    basis = "|".join(
        [
            str(payload.dealer_id),
            str(payload.channel),
            str(payload.direction),
            payload.occurred_at.isoformat(),
            (payload.raw_content or payload.normalized_content or "").strip(),
        ]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


@router.get("", response_model=list[InteractionOut])
def list_interactions(
    db: DbSession,
    dealer_id: int | None = None,
    channel: str | None = None,
    needs_review: bool | None = None,
    limit: int = 200,
):
    q = select(Interaction)
    if dealer_id is not None:
        q = q.where(Interaction.dealer_id == dealer_id)
    if channel is not None:
        q = q.where(Interaction.channel == channel)
    if needs_review is not None:
        q = q.where(Interaction.needs_review.is_(needs_review))
    return db.scalars(q.order_by(Interaction.occurred_at.desc()).limit(limit)).all()


@router.post("", response_model=InteractionOut, status_code=201)
def create_interaction(payload: InteractionIn, db: DbSession):
    digest = content_hash(payload)

    # Two independent dedupe guards, matching the ingestion contract: identical content
    # and identical provider identifier both mean "already have this".
    existing = db.scalars(
        select(Interaction).where(Interaction.content_hash == digest)
    ).first()
    if existing is not None:
        return existing

    if payload.source_identifier:
        existing = db.scalars(
            select(Interaction).where(
                Interaction.source_system == payload.source_system,
                Interaction.source_identifier == payload.source_identifier,
            )
        ).first()
        if existing is not None:
            return existing

    data = payload.model_dump()
    if not data.get("normalized_content"):
        data["normalized_content"] = data.get("raw_content")

    interaction = Interaction(**data, content_hash=digest, created_at=utcnow())
    db.add(interaction)
    db.flush()

    if interaction.dealer_id is not None:
        state_engine.refresh_one(db, interaction.dealer_id)
    return interaction


@router.get("/{interaction_id}", response_model=InteractionOut)
def read_interaction(interaction_id: int, db: DbSession):
    interaction = db.get(Interaction, interaction_id)
    if interaction is None:
        raise HTTPException(status_code=404, detail="interaction not found")
    return interaction


@router.delete("/{interaction_id}", status_code=204)
def delete_interaction(interaction_id: int, db: DbSession) -> None:
    interaction = db.get(Interaction, interaction_id)
    if interaction is None:
        raise HTTPException(status_code=404, detail="interaction not found")
    dealer_id = interaction.dealer_id
    db.delete(interaction)
    db.flush()
    if dealer_id is not None:
        state_engine.refresh_one(db, dealer_id)
