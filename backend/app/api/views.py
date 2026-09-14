"""Read-model endpoints: dashboard, dealer detail, comparison, canned queries."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import DbSession, get_dealer_or_404
from app.api.offers import _serialize as serialize_offer
from app.models import DraftMessage, StateTransition
from app.schemas.entities import DraftOut, StateTransitionOut
from app.schemas.views import (
    AnswerOut,
    BehaviorReportOut,
    ComparisonOut,
    DashboardOut,
    DealerDetailOut,
    RecommendationOut,
)
from app.services import behavior, comparison, dashboard, narrative, queries, recommender
from app.services import facts as fact_service
from app.services.context import build_context, build_contexts

router = APIRouter(prefix="/api", tags=["views"])


@router.get("/dashboard", response_model=DashboardOut)
def read_dashboard(db: DbSession):
    return dashboard.build(db)


@router.get("/dealers/{dealer_id}/detail", response_model=DealerDetailOut)
def dealer_detail(dealer_id: int, db: DbSession):
    dealer = get_dealer_or_404(db, dealer_id)
    ctx = build_context(db, dealer_id)
    contexts = list(build_contexts(db).values())
    benchmark = recommender.best_benchmark(contexts, excluding_dealer_id=dealer_id)
    rec = recommender.recommend(
        ctx, benchmark, deal_accepted_with=recommender.accepted_dealer(contexts)
    )

    price_ranked = sorted(
        (
            c
            for c in contexts
            if c.current_pricing and c.current_pricing.dealer_controlled_cents is not None
        ),
        key=lambda c: c.current_pricing.dealer_controlled_cents,
    )
    rank = next(
        (
            (i + 1, len(price_ranked))
            for i, c in enumerate(price_ranked)
            if c.dealer.id == dealer_id
        ),
        None,
    )
    report = behavior.report(ctx, price_rank=rank)
    owes, owes_reason = ctx.owes_response()
    state_def = ctx.state_defs.get(dealer.state_code)

    offer_history = [serialize_offer(db, o) for o in sorted(ctx.offers, key=lambda o: o.version)]
    current_offer = ctx.current_offer

    return DealerDetailOut(
        dealer=dealer,
        state_label=state_def.label if state_def else dealer.state_code,
        contacts=dealer.contacts,
        vehicles=dealer.vehicles,
        current_offer=serialize_offer(db, current_offer) if current_offer else None,
        offer_history=offer_history,
        offer_progression=dashboard.offer_progression(ctx),
        timeline=dashboard.timeline(ctx),
        questions=ctx.questions,
        commitments=ctx.commitments,
        contradictions=ctx.contradictions,
        facts=fact_service.for_dealer(db, dealer_id, include_superseded=True),
        signals=ctx.signals,
        behavior=BehaviorReportOut(
            dealer_id=report.dealer_id,
            transparency=report.transparency,
            responsiveness=report.responsiveness,
            price_competitiveness=report.price_competitiveness,
            friction_points=report.friction_points,
            friction_reasons=report.friction_reasons,
            friction_label=report.friction_label,
        ),
        recommendation=RecommendationOut.model_validate(rec),
        drafts=[
            DraftOut.model_validate(d)
            for d in db.scalars(
                select(DraftMessage)
                .where(DraftMessage.dealer_id == dealer_id)
                .order_by(DraftMessage.created_at.desc())
            ).all()
        ],
        transitions=[
            StateTransitionOut.model_validate(t)
            for t in db.scalars(
                select(StateTransition)
                .where(StateTransition.dealer_id == dealer_id)
                .order_by(StateTransition.created_at.desc())
            ).all()
        ],
        owes_response=str(owes),
        owes_reason=owes_reason,
        idle_hours=ctx.idle_hours,
        summary=narrative.summarize(ctx),
    )


@router.get("/compare", response_model=ComparisonOut)
def compare(db: DbSession, dealer_ids: list[int] | None = Query(default=None)):
    return comparison.compare(db, dealer_ids)


@router.get("/questions/canned", response_model=list[str])
def canned_questions():
    return sorted(queries.CANNED)


@router.get("/ask/{key}", response_model=AnswerOut)
def ask(key: str, db: DbSession):
    """Run one of the canned structural questions. Exact SQL, no model involved."""
    fn = queries.CANNED.get(key)
    if fn is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown question {key!r}; available: {', '.join(sorted(queries.CANNED))}",
        )
    return fn(db)


@router.get("/ask/changed/{hours}", response_model=AnswerOut)
def ask_changed(hours: int, db: DbSession):
    return queries.changed_since(db, hours=hours)


@router.get("/ask/add-on/{needle}", response_model=AnswerOut)
def ask_add_on(needle: str, db: DbSession):
    return queries.add_on_by_name(db, needle)


@router.get("/dealers/{dealer_id}/first-quote", response_model=AnswerOut)
def first_quote(dealer_id: int, db: DbSession):
    return queries.first_quote(db, dealer_id)
