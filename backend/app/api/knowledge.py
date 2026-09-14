"""Facts, provenance, questions, commitments, contradictions."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import DbSession
from app.enums import CommitmentStatus, QuestionStatus
from app.models import Commitment, Contradiction, Fact, Question
from app.models.base import utcnow
from app.schemas.entities import (
    CommitmentIn,
    CommitmentOut,
    CommitmentPatch,
    ContradictionOut,
    ContradictionPatch,
    FactIn,
    FactOut,
    ProvenanceOut,
    QuestionIn,
    QuestionOut,
    QuestionPatch,
)
from app.services import contradictions as contradiction_service
from app.services import facts as fact_service

router = APIRouter(prefix="/api", tags=["knowledge"])


# --------------------------------------------------------------------- facts
@router.get("/facts", response_model=list[FactOut])
def list_facts(
    db: DbSession,
    dealer_id: int | None = None,
    subject_type: str | None = None,
    subject_id: int | None = None,
    attribute: str | None = None,
    include_superseded: bool = False,
):
    q = select(Fact)
    if dealer_id is not None:
        q = q.where(Fact.dealer_id == dealer_id)
    if subject_type is not None:
        q = q.where(Fact.subject_type == subject_type)
    if subject_id is not None:
        q = q.where(Fact.subject_id == subject_id)
    if attribute is not None:
        q = q.where(Fact.attribute == attribute)
    if not include_superseded:
        q = q.where(Fact.status == "CURRENT")
    return db.scalars(q.order_by(Fact.attribute, Fact.created_at.desc())).all()


@router.post("/facts", response_model=FactOut, status_code=201)
def create_fact(payload: FactIn, db: DbSession):
    return fact_service.record(db, **payload.model_dump())


@router.get("/facts/{fact_id}/provenance", response_model=ProvenanceOut)
def fact_provenance(fact_id: int, db: DbSession):
    """"Why does the system believe this?" — the source and the full change history."""
    result = fact_service.provenance(db, fact_id)
    if result is None:
        raise HTTPException(status_code=404, detail="fact not found")
    return ProvenanceOut(
        fact=FactOut.model_validate(result.fact),
        interaction=result.interaction,
        superseded=[FactOut.model_validate(f) for f in result.superseded],
    )


# ----------------------------------------------------------------- questions
@router.get("/questions", response_model=list[QuestionOut])
def list_questions(db: DbSession, dealer_id: int | None = None, status: str | None = None):
    q = select(Question)
    if dealer_id is not None:
        q = q.where(Question.dealer_id == dealer_id)
    if status is not None:
        q = q.where(Question.status == status)
    return db.scalars(q.order_by(Question.asked_at.desc().nullslast())).all()


@router.post("/questions", response_model=QuestionOut, status_code=201)
def create_question(payload: QuestionIn, db: DbSession):
    data = payload.model_dump()
    data.setdefault("asked_at", utcnow())
    question = Question(**data)
    db.add(question)
    db.flush()
    return question


@router.patch("/questions/{question_id}", response_model=QuestionOut)
def update_question(question_id: int, payload: QuestionPatch, db: DbSession):
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="question not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(question, key, value)
    if question.status == QuestionStatus.ANSWERED and question.answered_at is None:
        question.answered_at = utcnow()
    db.flush()
    return question


# --------------------------------------------------------------- commitments
@router.get("/commitments", response_model=list[CommitmentOut])
def list_commitments(db: DbSession, dealer_id: int | None = None, status: str | None = None):
    q = select(Commitment)
    if dealer_id is not None:
        q = q.where(Commitment.dealer_id == dealer_id)
    if status is not None:
        q = q.where(Commitment.status == status)
    return db.scalars(q.order_by(Commitment.due_at.asc().nullslast())).all()


@router.post("/commitments", response_model=CommitmentOut, status_code=201)
def create_commitment(payload: CommitmentIn, db: DbSession):
    commitment = Commitment(**payload.model_dump())
    db.add(commitment)
    db.flush()
    return commitment


@router.patch("/commitments/{commitment_id}", response_model=CommitmentOut)
def update_commitment(commitment_id: int, payload: CommitmentPatch, db: DbSession):
    commitment = db.get(Commitment, commitment_id)
    if commitment is None:
        raise HTTPException(status_code=404, detail="commitment not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(commitment, key, value)
    db.flush()
    return commitment


@router.get("/commitments/overdue", response_model=list[CommitmentOut])
def overdue_commitments(db: DbSession):
    return db.scalars(
        select(Commitment).where(
            Commitment.status == CommitmentStatus.OPEN,
            Commitment.due_at.is_not(None),
            Commitment.due_at < utcnow(),
        )
    ).all()


# ------------------------------------------------------------ contradictions
@router.get("/contradictions", response_model=list[ContradictionOut])
def list_contradictions(db: DbSession, dealer_id: int | None = None, status: str | None = None):
    q = select(Contradiction)
    if dealer_id is not None:
        q = q.where(Contradiction.dealer_id == dealer_id)
    if status is not None:
        q = q.where(Contradiction.status == status)
    return db.scalars(q.order_by(Contradiction.detected_at.desc())).all()


@router.post("/contradictions/detect", response_model=list[ContradictionOut])
def detect_contradictions(db: DbSession, dealer_id: int | None = None):
    if dealer_id is None:
        return contradiction_service.detect_all(db)
    from app.services.context import build_context

    return contradiction_service.detect_for_dealer(db, build_context(db, dealer_id))


@router.patch("/contradictions/{contradiction_id}", response_model=ContradictionOut)
def update_contradiction(contradiction_id: int, payload: ContradictionPatch, db: DbSession):
    row = db.get(Contradiction, contradiction_id)
    if row is None:
        raise HTTPException(status_code=404, detail="contradiction not found")
    row.status = payload.status
    if payload.resolution_note is not None:
        row.resolution_note = payload.resolution_note
    db.flush()
    return row
