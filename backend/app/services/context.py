"""Per-dealer derived context.

Nothing in here is stored. "Who owes a response", "how idle is this", "what is the
current offer" are computed from the interaction and offer tables every time they are
asked for, because a cached copy would go stale on every ingest and create a second
source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.enums import (
    CommitmentStatus,
    ContradictionStatus,
    Direction,
    Party,
    QuestionStatus,
)
from app.models import (
    BehaviorSignal,
    BuyerProfile,
    Commitment,
    Contradiction,
    Dealer,
    Interaction,
    NegotiationStateDef,
    Offer,
    Question,
)
from app.models.base import utcnow
from app.services import pricing
from app.services.pricing import PricingResult


@dataclass
class DealerContext:
    dealer: Dealer
    profile: BuyerProfile | None
    now: datetime

    interactions: list[Interaction] = field(default_factory=list)
    offers: list[Offer] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    commitments: list[Commitment] = field(default_factory=list)
    contradictions: list[Contradiction] = field(default_factory=list)
    signals: list[BehaviorSignal] = field(default_factory=list)
    state_defs: dict[str, NegotiationStateDef] = field(default_factory=dict)

    # ------------------------------------------------------------------ offers
    @property
    def offer_history(self) -> list[PricingResult]:
        rate = self.profile.expected_tax_rate_bp if self.profile else None
        return [
            pricing.compute(offer, expected_tax_rate_bp=rate)
            for offer in sorted(self.offers, key=lambda o: o.version)
        ]

    @property
    def current_offer(self) -> Offer | None:
        current = [o for o in self.offers if o.is_current]
        if current:
            return max(current, key=lambda o: (o.version, o.quoted_at))
        return max(self.offers, key=lambda o: (o.version, o.quoted_at)) if self.offers else None

    @property
    def current_pricing(self) -> PricingResult | None:
        offer = self.current_offer
        if offer is None:
            return None
        rate = self.profile.expected_tax_rate_bp if self.profile else None
        return pricing.compute(offer, expected_tax_rate_bp=rate)

    @property
    def has_offer(self) -> bool:
        return self.current_offer is not None

    @property
    def has_written_otd(self) -> bool:
        p = self.current_pricing
        return bool(p and p.quoted_otd_cents is not None)

    # ------------------------------------------------------- communication flow
    @property
    def inbound(self) -> list[Interaction]:
        return [i for i in self.interactions if i.direction == Direction.INBOUND]

    @property
    def outbound(self) -> list[Interaction]:
        return [i for i in self.interactions if i.direction == Direction.OUTBOUND]

    @property
    def last_inbound(self) -> Interaction | None:
        return max(self.inbound, key=lambda i: i.occurred_at, default=None)

    @property
    def last_outbound(self) -> Interaction | None:
        return max(self.outbound, key=lambda i: i.occurred_at, default=None)

    @property
    def last_interaction(self) -> Interaction | None:
        candidates = [i for i in self.interactions if i.direction != Direction.INTERNAL]
        return max(candidates, key=lambda i: i.occurred_at, default=None)

    @property
    def idle_hours(self) -> float | None:
        last = self.last_interaction
        if last is None:
            return None
        return max(0.0, (self.now - last.occurred_at).total_seconds() / 3600)

    @property
    def idle_days(self) -> float | None:
        hours = self.idle_hours
        return hours / 24 if hours is not None else None

    @property
    def open_questions(self) -> list[Question]:
        return [q for q in self.questions if q.status == QuestionStatus.OPEN]

    @property
    def open_buyer_questions(self) -> list[Question]:
        return [q for q in self.open_questions if q.asked_by == Party.BUYER]

    @property
    def open_dealer_questions(self) -> list[Question]:
        return [q for q in self.open_questions if q.asked_by == Party.DEALER]

    @property
    def open_commitments(self) -> list[Commitment]:
        return [c for c in self.commitments if c.status == CommitmentStatus.OPEN]

    @property
    def open_contradictions(self) -> list[Contradiction]:
        return [
            c
            for c in self.contradictions
            if c.status in (ContradictionStatus.OPEN, ContradictionStatus.ACKNOWLEDGED)
        ]

    def owes_response(self) -> tuple[str, str]:
        """(party, reason). Derived, never stored as an opinion."""
        state_def = self.state_defs.get(self.dealer.state_code)
        if state_def is not None and state_def.is_terminal:
            return Party.NOBODY, f"Negotiation is {state_def.label.lower()}."
        if not self.interactions:
            return Party.BUYER, "No contact has been made yet."

        # An unanswered question outranks message order: a dealer who replied without
        # answering what was asked still owes an answer.
        if self.open_buyer_questions:
            count = len(self.open_buyer_questions)
            return Party.DEALER, (
                f"{count} question{'s' if count > 1 else ''} you asked "
                f"{'are' if count > 1 else 'is'} still unanswered."
            )

        last = self.last_interaction
        if last is None:
            return Party.BUYER, "No contact has been made yet."
        if last.direction == Direction.INBOUND:
            if self.open_dealer_questions:
                return Party.BUYER, "The dealer asked you something you haven't answered."
            return Party.BUYER, "The dealer sent the most recent message."
        return Party.DEALER, "You sent the most recent message."

    @property
    def is_terminal(self) -> bool:
        state_def = self.state_defs.get(self.dealer.state_code)
        return bool(state_def and state_def.is_terminal)


def _state_defs(db: Session) -> dict[str, NegotiationStateDef]:
    return {s.code: s for s in db.scalars(select(NegotiationStateDef)).all()}


def get_profile(db: Session) -> BuyerProfile | None:
    return db.scalars(select(BuyerProfile).order_by(BuyerProfile.id).limit(1)).first()


def build_contexts(
    db: Session,
    *,
    dealer_ids: list[int] | None = None,
    now: datetime | None = None,
) -> dict[int, DealerContext]:
    """Bulk-load context for many dealers in a fixed number of queries."""
    now = now or utcnow()
    profile = get_profile(db)
    defs = _state_defs(db)

    dealer_q = select(Dealer)
    if dealer_ids is not None:
        dealer_q = dealer_q.where(Dealer.id.in_(dealer_ids))
    dealers = db.scalars(dealer_q.order_by(Dealer.name)).all()
    ids = [d.id for d in dealers]

    contexts = {
        d.id: DealerContext(dealer=d, profile=profile, now=now, state_defs=defs) for d in dealers
    }
    if not ids:
        return contexts

    for row in db.scalars(
        select(Interaction).where(Interaction.dealer_id.in_(ids)).order_by(Interaction.occurred_at)
    ).all():
        contexts[row.dealer_id].interactions.append(row)

    for row in db.scalars(
        select(Offer).options(selectinload(Offer.lines)).where(Offer.dealer_id.in_(ids))
    ).all():
        contexts[row.dealer_id].offers.append(row)

    for model, attr in (
        (Question, "questions"),
        (Commitment, "commitments"),
        (Contradiction, "contradictions"),
        (BehaviorSignal, "signals"),
    ):
        for row in db.scalars(select(model).where(model.dealer_id.in_(ids))).all():
            getattr(contexts[row.dealer_id], attr).append(row)

    return contexts


def build_context(db: Session, dealer_id: int, *, now: datetime | None = None) -> DealerContext:
    contexts = build_contexts(db, dealer_ids=[dealer_id], now=now)
    if dealer_id not in contexts:
        raise KeyError(f"dealer {dealer_id} not found")
    return contexts[dealer_id]
