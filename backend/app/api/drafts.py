from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import DbSession, get_dealer_or_404
from app.enums import DraftStatus
from app.models import DraftMessage, Notification
from app.models.base import utcnow
from app.schemas.entities import DraftIn, DraftOut, DraftPatch, NotificationOut
from app.services import notifications as notification_service
from app.services import recommender
from app.services.context import build_context, build_contexts

router = APIRouter(prefix="/api", tags=["drafts"])


@router.get("/drafts", response_model=list[DraftOut])
def list_drafts(db: DbSession, dealer_id: int | None = None, status: str | None = None):
    q = select(DraftMessage)
    if dealer_id is not None:
        q = q.where(DraftMessage.dealer_id == dealer_id)
    if status is not None:
        q = q.where(DraftMessage.status == status)
    return db.scalars(q.order_by(DraftMessage.created_at.desc())).all()


@router.post("/drafts", response_model=DraftOut, status_code=201)
def create_draft(payload: DraftIn, db: DbSession):
    get_dealer_or_404(db, payload.dealer_id)
    draft = DraftMessage(**payload.model_dump())
    db.add(draft)
    db.flush()
    return draft


@router.post("/dealers/{dealer_id}/drafts/generate", response_model=DraftOut, status_code=201)
def generate_draft(dealer_id: int, db: DbSession):
    """Generate a draft from the structured negotiation state.

    Phase 1 builds this from rules, so it works with no model and no network. Phase 3
    swaps the body for an LLM-written one — the rationale and the numbers it cites still
    come from here.
    """
    dealer = get_dealer_or_404(db, dealer_id)
    ctx = build_context(db, dealer_id)
    contexts = list(build_contexts(db).values())
    benchmark = recommender.best_benchmark(contexts, excluding_dealer_id=dealer_id)
    rec = recommender.recommend(
        ctx, benchmark, deal_accepted_with=recommender.accepted_dealer(contexts)
    )

    body = rec.suggested_message or (
        f"{rec.headline}\n\n{rec.detail}\n\n"
        "(No pre-written message for this situation yet — edit before sending.)"
    )
    contact = next((c for c in dealer.contacts if c.is_primary), None) or (
        dealer.contacts[0] if dealer.contacts else None
    )
    last_inbound = ctx.last_inbound

    draft = DraftMessage(
        dealer_id=dealer_id,
        contact_id=contact.id if contact else None,
        in_reply_to_interaction_id=last_inbound.id if last_inbound else None,
        subject=(
            f"Re: {last_inbound.subject}"
            if last_inbound and last_inbound.subject
            else "Following up on pricing"
        ),
        body=body,
        rationale=f"{rec.headline} — {rec.detail}",
        strategy_notes="\n".join(f"{k}: {v}" for k, v in rec.supporting_numbers.items()),
        generated_by="RULE",
    )
    db.add(draft)
    db.flush()
    return draft


@router.patch("/drafts/{draft_id}", response_model=DraftOut)
def update_draft(draft_id: int, payload: DraftPatch, db: DbSession):
    draft = db.get(DraftMessage, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="draft not found")
    data = payload.model_dump(exclude_unset=True)

    # Sending is deliberately not implemented. Architected for, not enabled.
    if data.get("status") == DraftStatus.SENT:
        raise HTTPException(
            status_code=400,
            detail=(
                "Automatic sending is not enabled. Copy the approved draft into your "
                "mail client and send it yourself."
            ),
        )
    if "body" in data or "subject" in data:
        draft.edited_by_user = True
    for key, value in data.items():
        setattr(draft, key, value)
    if draft.status == DraftStatus.APPROVED and draft.approved_at is None:
        draft.approved_at = utcnow()
    db.flush()
    return draft


@router.delete("/drafts/{draft_id}", status_code=204)
def delete_draft(draft_id: int, db: DbSession) -> None:
    draft = db.get(DraftMessage, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="draft not found")
    db.delete(draft)


# ------------------------------------------------------------- notifications
@router.get("/notifications", response_model=list[NotificationOut])
def list_notifications(db: DbSession, limit: int = 50):
    return notification_service.unread(db, limit=limit)


@router.post("/notifications/refresh", response_model=list[NotificationOut])
def refresh_notifications(db: DbSession):
    return notification_service.refresh(db)


@router.post("/notifications/{notification_id}/dismiss", response_model=NotificationOut)
def dismiss_notification(notification_id: int, db: DbSession):
    row = db.get(Notification, notification_id)
    if row is None:
        raise HTTPException(status_code=404, detail="notification not found")
    row.dismissed_at = utcnow()
    db.flush()
    return row
