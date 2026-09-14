"""Read-model schemas for the dashboard, dealer detail, and comparison screens."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel
from app.schemas.entities import (
    BehaviorSignalOut,
    CommitmentOut,
    ContactOut,
    ContradictionOut,
    DealerOut,
    DraftOut,
    FactOut,
    OfferOut,
    QuestionOut,
    StateTransitionOut,
    VehicleOut,
)


class DashboardRowOut(ORMModel):
    dealer_id: int
    dealer_name: str
    is_local: bool
    distance_miles: float | None = None
    primary_contact: str | None = None
    primary_contact_role: str | None = None
    contact_is_automated: bool
    vehicle_summary: str | None = None
    vin: str | None = None
    selling_price_cents: int | None = None
    dealer_controlled_cents: int | None = None
    otd_cents: int | None = None
    otd_is_written: bool
    otd_variance_cents: int | None = None
    offer_version: int | None = None
    state_code: str
    state_label: str
    state_is_pinned: bool
    last_interaction_at: str | None = None
    last_interaction_channel: str | None = None
    last_interaction_direction: str | None = None
    idle_hours: float | None = None
    owes_response: str
    owes_reason: str
    next_action_code: str
    next_action: str
    next_action_detail: str
    next_action_priority: int
    next_action_severity: str
    unresolved_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    friction_points: float = 0.0
    friction_reasons: list[str] = Field(default_factory=list)
    is_best_otd: bool = False
    gap_to_best_cents: int | None = None


class DashboardSummaryOut(ORMModel):
    dealers_total: int
    dealers_contacted: int
    dealers_responded: int
    dealers_silent: int
    dealers_with_offer: int
    best_otd_cents: int | None = None
    best_otd_dealer: str | None = None
    best_dealer_controlled_cents: int | None = None
    best_dealer_controlled_dealer: str | None = None
    you_owe_count: int
    dealer_owes_count: int
    open_contradictions: int
    recent_changes: list[str] = Field(default_factory=list)


class DashboardOut(BaseModel):
    summary: DashboardSummaryOut
    rows: list[DashboardRowOut]


class ScoredDimensionOut(ORMModel):
    dimension: str
    score: int | None = None
    reasons: list[str] = Field(default_factory=list)
    basis: str | None = None


class BehaviorReportOut(ORMModel):
    dealer_id: int
    transparency: ScoredDimensionOut
    responsiveness: ScoredDimensionOut
    price_competitiveness: ScoredDimensionOut
    friction_points: float
    friction_reasons: list[str] = Field(default_factory=list)
    friction_label: str


class RecommendationOut(ORMModel):
    code: str
    headline: str
    detail: str
    priority: int
    severity: str
    supporting_numbers: dict[str, str] = Field(default_factory=dict)
    suggested_message: str | None = None


class DealerDetailOut(BaseModel):
    dealer: DealerOut
    state_label: str
    contacts: list[ContactOut]
    vehicles: list[VehicleOut]
    current_offer: OfferOut | None = None
    offer_history: list[OfferOut] = Field(default_factory=list)
    offer_progression: list[dict[str, Any]] = Field(default_factory=list)
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    questions: list[QuestionOut] = Field(default_factory=list)
    commitments: list[CommitmentOut] = Field(default_factory=list)
    contradictions: list[ContradictionOut] = Field(default_factory=list)
    facts: list[FactOut] = Field(default_factory=list)
    signals: list[BehaviorSignalOut] = Field(default_factory=list)
    behavior: BehaviorReportOut
    recommendation: RecommendationOut
    drafts: list[DraftOut] = Field(default_factory=list)
    transitions: list[StateTransitionOut] = Field(default_factory=list)
    owes_response: str
    owes_reason: str
    idle_hours: float | None = None
    # Deterministic, non-LLM narrative of where this negotiation stands.
    summary: str


class ComparisonRowOut(ORMModel):
    dealer_id: int
    dealer_name: str
    is_local: bool
    distance_miles: float | None = None
    state_code: str
    vehicle_summary: str | None = None
    vin: str | None = None
    exterior_color: str | None = None
    mileage: int | None = None
    is_demo: bool | None = None
    msrp_cents: int | None = None
    selling_price_cents: int | None = None
    discount_from_msrp_cents: int | None = None
    dealer_fees_total_cents: int | None = None
    fees_disclosed: bool = True
    add_ons_total_cents: int | None = None
    dealer_controlled_cents: int | None = None
    clean_dealer_controlled_cents: int | None = None
    tax_cents: int | None = None
    registration_cents: int | None = None
    government_total_cents: int | None = None
    otd_cents: int | None = None
    otd_variance_cents: int | None = None
    financing_required: bool | None = None
    financing_provider: str | None = None
    apr_bp: int | None = None
    financing_term_months: int | None = None
    friction_points: float
    friction_reasons: list[str] = Field(default_factory=list)
    unresolved_issues: list[str] = Field(default_factory=list)
    cleanliness_score: int
    cleanliness_reasons: list[str] = Field(default_factory=list)
    offer_version: int | None = None
    quoted_at: str | None = None


class WinnerOut(ORMModel):
    label: str
    dealer_id: int | None = None
    dealer_name: str | None = None
    value: str
    explanation: str


class ComparisonOut(BaseModel):
    rows: list[ComparisonRowOut]
    winners: list[WinnerOut]
    notes: list[str] = Field(default_factory=list)


class AnswerOut(BaseModel):
    """Structured answer to one of the canned questions. Rows are real SQL results."""

    question: str
    answer: str
    rows: list[dict[str, Any]] = Field(default_factory=list)
