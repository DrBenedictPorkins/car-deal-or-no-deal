from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.enums import (
    ActorKind,
    Channel,
    CommitmentStatus,
    ContactRole,
    Direction,
    ExtractionMethod,
    FeeKind,
    Party,
    PurchaseType,
    QuestionStatus,
    Severity,
    SubjectType,
    VehicleCondition,
)
from app.schemas.common import ORMModel, Timestamped


# --------------------------------------------------------------------- profile
class BuyerProfileIn(BaseModel):
    display_name: str | None = None
    email: str | None = None
    phone: str | None = None
    purchase_type: PurchaseType = PurchaseType.NEW_ONLY
    target_year: int | None = None
    target_make: str | None = None
    target_model: str | None = None
    target_trim: str | None = None
    target_powertrain: str | None = None
    color_preferences: str | None = None
    excluded_colors: str | None = None
    has_trade_in: bool = False
    cash_available: bool = True
    financing_acceptable: str = "ONLY_IF_ADVANTAGEOUS"
    wants_add_ons: bool = False
    wants_maintenance_plan: bool = False
    registration_state: str | None = None
    zip_code: str | None = None
    expected_tax_rate_bp: int | None = Field(
        default=None, description="Basis points. 600 = 6.00%."
    )
    max_distance_miles: int | None = None
    local_dealer_premium_cents: int = 0
    contact_preference: str = "EMAIL_PREFERRED"
    avoid_phone_calls: bool = True
    avoid_dealership_visits: bool = True
    follow_up_after_hours: int = 48
    no_response_after_days: int = 7
    notes: str | None = None


class BuyerProfileOut(BuyerProfileIn, Timestamped):
    id: int


# --------------------------------------------------------------------- dealer
class DealerIn(BaseModel):
    name: str
    address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    website: str | None = None
    phone: str | None = None
    email_domains: str | None = None
    distance_miles: float | None = None
    is_local: bool = False
    notes: str | None = None


class DealerPatch(BaseModel):
    name: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    website: str | None = None
    phone: str | None = None
    email_domains: str | None = None
    distance_miles: float | None = None
    is_local: bool | None = None
    notes: str | None = None
    status: str | None = None


class DealerOut(Timestamped):
    id: int
    name: str
    address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    website: str | None = None
    phone: str | None = None
    email_domains: str | None = None
    distance_miles: float | None = None
    is_local: bool
    state_code: str
    state_is_pinned: bool
    state_changed_at: datetime | None = None
    first_contacted_at: datetime | None = None
    status: str
    notes: str | None = None


class StateSet(BaseModel):
    state_code: str
    reason: str | None = None
    pin: bool = True


class StateDefOut(ORMModel):
    code: str
    label: str
    description: str | None = None
    sort_order: int
    is_terminal: bool
    is_active_pipeline: bool
    is_builtin: bool


class StateTransitionOut(ORMModel):
    id: int
    dealer_id: int
    from_state: str | None = None
    to_state: str
    reason_code: str | None = None
    reason_text: str | None = None
    triggered_by: str
    rule_id: str | None = None
    interaction_id: int | None = None
    was_suppressed_by_pin: bool
    created_at: datetime


# -------------------------------------------------------------------- contact
class ContactIn(BaseModel):
    dealer_id: int
    name: str | None = None
    title: str | None = None
    email: str | None = None
    phone: str | None = None
    role: ContactRole = ContactRole.UNKNOWN
    actor_kind: ActorKind = ActorKind.UNKNOWN
    automation_evidence: str | None = None
    is_primary: bool = False
    notes: str | None = None


class ContactPatch(BaseModel):
    name: str | None = None
    title: str | None = None
    email: str | None = None
    phone: str | None = None
    role: ContactRole | None = None
    actor_kind: ActorKind | None = None
    automation_evidence: str | None = None
    is_primary: bool | None = None
    notes: str | None = None


class ContactOut(Timestamped):
    id: int
    dealer_id: int
    name: str | None = None
    title: str | None = None
    email: str | None = None
    phone: str | None = None
    role: str
    actor_kind: str
    automation_evidence: str | None = None
    is_primary: bool
    notes: str | None = None


# -------------------------------------------------------------------- vehicle
class VehicleIn(BaseModel):
    dealer_id: int
    year: int | None = None
    make: str | None = None
    model: str | None = None
    trim: str | None = None
    body_style: str | None = None
    vin: str | None = None
    stock_number: str | None = None
    exterior_color: str | None = None
    interior_color: str | None = None
    mileage: int | None = None
    msrp_cents: int | None = None
    condition: VehicleCondition = VehicleCondition.NEW
    is_demo: bool | None = None
    is_loaner: bool | None = None
    damage_history: str | None = None
    availability: str | None = None
    notes: str | None = None


class VehiclePatch(VehicleIn):
    dealer_id: int | None = None
    condition: VehicleCondition | None = None


class VehicleOut(Timestamped):
    id: int
    dealer_id: int
    year: int | None = None
    make: str | None = None
    model: str | None = None
    trim: str | None = None
    body_style: str | None = None
    vin: str | None = None
    stock_number: str | None = None
    exterior_color: str | None = None
    interior_color: str | None = None
    mileage: int | None = None
    msrp_cents: int | None = None
    condition: str
    is_demo: bool | None = None
    is_loaner: bool | None = None
    damage_history: str | None = None
    availability: str | None = None
    notes: str | None = None


# ---------------------------------------------------------------- interaction
class InteractionIn(BaseModel):
    dealer_id: int | None = None
    contact_id: int | None = None
    vehicle_id: int | None = None
    channel: Channel
    direction: Direction
    occurred_at: datetime
    subject: str | None = None
    raw_content: str | None = None
    normalized_content: str | None = None
    actor_kind: ActorKind = ActorKind.UNKNOWN
    classification_reason: str | None = None
    is_quote_bearing: bool = False
    source_system: str = "manual"
    source_identifier: str | None = None
    source_thread_identifier: str | None = None


class InteractionOut(ORMModel):
    id: int
    dealer_id: int | None = None
    contact_id: int | None = None
    vehicle_id: int | None = None
    channel: str
    direction: str
    occurred_at: datetime
    subject: str | None = None
    raw_content: str | None = None
    normalized_content: str | None = None
    quoted_content: str | None = None
    summary: str | None = None
    actor_kind: str
    classification_reason: str | None = None
    is_quote_bearing: bool
    source_system: str
    source_identifier: str | None = None
    source_thread_identifier: str | None = None
    content_hash: str | None = None
    needs_review: bool
    created_at: datetime


# ---------------------------------------------------------------------- offer
class OfferLineIn(BaseModel):
    kind: FeeKind = FeeKind.ADD_ON
    name: str
    price_cents: int = 0
    is_dealer_controlled: bool = True
    is_taxable: bool | None = None
    mandatory_claimed: bool | None = None
    already_installed: bool | None = None
    user_wants: bool = False
    removable_confirmed: bool | None = None
    notes: str | None = None


class OfferLineOut(OfferLineIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    offer_id: int


class OfferIn(BaseModel):
    dealer_id: int
    vehicle_id: int | None = None
    interaction_id: int | None = None
    quoted_at: datetime
    msrp_cents: int | None = None
    advertised_price_cents: int | None = None
    selling_price_cents: int | None = None
    discount_cents: int | None = None
    destination_cents: int | None = None
    doc_fee_cents: int | None = None
    processing_fee_cents: int | None = None
    other_taxable_fees_cents: int | None = None
    other_non_tax_fees_cents: int | None = None
    tax_cents: int | None = None
    registration_cents: int | None = None
    title_fee_cents: int | None = None
    quoted_otd_cents: int | None = None
    financing_required: bool | None = None
    financing_provider: str | None = None
    apr_bp: int | None = None
    financing_term_months: int | None = None
    minimum_financed_cents: int | None = None
    minimum_loan_months: int | None = None
    prepayment_penalty: bool | None = None
    discount_clawback: bool | None = None
    clawback_terms: str | None = None
    expires_at: datetime | None = None
    deadline_note: str | None = None
    notes: str | None = None
    lines: list[OfferLineIn] = Field(default_factory=list)


class PricingOut(BaseModel):
    offer_id: int
    version: int
    quoted_at: datetime
    msrp_cents: int | None
    selling_price_cents: int | None
    add_ons_total_cents: int
    dealer_fees_total_cents: int
    government_total_cents: int
    dealer_controlled_cents: int | None
    computed_otd_cents: int | None
    quoted_otd_cents: int | None
    effective_otd_cents: int | None
    otd_variance_cents: int | None
    otd_reconciled: bool
    discount_from_msrp_cents: int | None
    taxable_base_cents: int | None
    implied_tax_rate_bp: int | None
    expected_tax_rate_bp: int | None
    tax_rate_variance_bp: int | None
    unwanted_add_ons_cents: int
    clean_dealer_controlled_cents: int | None
    clean_otd_cents: int | None
    warnings: list[str]
    is_complete: bool


class OfferOut(ORMModel):
    id: int
    dealer_id: int
    vehicle_id: int | None = None
    interaction_id: int | None = None
    version: int
    supersedes_offer_id: int | None = None
    is_current: bool
    quoted_at: datetime
    msrp_cents: int | None = None
    advertised_price_cents: int | None = None
    selling_price_cents: int | None = None
    discount_cents: int | None = None
    destination_cents: int | None = None
    doc_fee_cents: int | None = None
    processing_fee_cents: int | None = None
    other_taxable_fees_cents: int | None = None
    other_non_tax_fees_cents: int | None = None
    tax_cents: int | None = None
    registration_cents: int | None = None
    title_fee_cents: int | None = None
    quoted_otd_cents: int | None = None
    financing_required: bool | None = None
    financing_provider: str | None = None
    apr_bp: int | None = None
    financing_term_months: int | None = None
    minimum_financed_cents: int | None = None
    minimum_loan_months: int | None = None
    prepayment_penalty: bool | None = None
    discount_clawback: bool | None = None
    clawback_terms: str | None = None
    expires_at: datetime | None = None
    deadline_note: str | None = None
    confidence: str
    needs_review: bool
    notes: str | None = None
    created_at: datetime
    lines: list[OfferLineOut] = Field(default_factory=list)
    pricing: PricingOut | None = None


# ----------------------------------------------------------- facts / questions
class FactIn(BaseModel):
    subject_type: SubjectType
    subject_id: int
    attribute: str
    dealer_id: int | None = None
    value_text: str | None = None
    value_number: float | None = None
    value_bool: bool | None = None
    value_datetime: datetime | None = None
    value_unit: str | None = None
    interaction_id: int | None = None
    offer_id: int | None = None
    quote: str | None = None
    quote_start: int | None = None
    quote_end: int | None = None
    method: ExtractionMethod = ExtractionMethod.MANUAL
    confidence: float | None = None
    asserted_by_party: Party = Party.DEALER
    observed_at: datetime | None = None


class FactOut(ORMModel):
    id: int
    subject_type: str
    subject_id: int
    attribute: str
    dealer_id: int | None = None
    value_text: str | None = None
    value_number: float | None = None
    value_bool: bool | None = None
    value_datetime: datetime | None = None
    value_unit: str | None = None
    display_value: str | None = None
    status: str
    superseded_by_id: int | None = None
    method: str
    confidence: float | None = None
    interaction_id: int | None = None
    offer_id: int | None = None
    quote: str | None = None
    quote_start: int | None = None
    quote_end: int | None = None
    asserted_by_party: str
    observed_at: datetime | None = None
    created_at: datetime


class ProvenanceOut(BaseModel):
    fact: FactOut
    interaction: InteractionOut | None = None
    superseded: list[FactOut] = Field(default_factory=list)


class QuestionIn(BaseModel):
    dealer_id: int
    interaction_id: int | None = None
    asked_by: Party = Party.BUYER
    text: str
    topic: str | None = None
    importance: Severity = Severity.INFO
    asked_at: datetime | None = None


class QuestionPatch(BaseModel):
    status: QuestionStatus | None = None
    answered_interaction_id: int | None = None
    answer_summary: str | None = None


class QuestionOut(ORMModel):
    id: int
    dealer_id: int
    interaction_id: int | None = None
    asked_by: str
    text: str
    topic: str | None = None
    importance: str
    status: str
    answered_interaction_id: int | None = None
    answer_summary: str | None = None
    asked_at: datetime | None = None
    answered_at: datetime | None = None


class CommitmentIn(BaseModel):
    dealer_id: int
    party: Party = Party.DEALER
    interaction_id: int | None = None
    text: str
    due_at: datetime | None = None


class CommitmentPatch(BaseModel):
    status: CommitmentStatus | None = None
    evidence_interaction_id: int | None = None
    due_at: datetime | None = None


class CommitmentOut(ORMModel):
    id: int
    dealer_id: int
    party: str
    interaction_id: int | None = None
    text: str
    due_at: datetime | None = None
    status: str
    evidence_interaction_id: int | None = None
    created_at: datetime


class ContradictionOut(ORMModel):
    id: int
    dealer_id: int
    attribute: str | None = None
    kind: str
    severity: str
    fact_a_id: int | None = None
    fact_b_id: int | None = None
    interaction_a_id: int | None = None
    interaction_b_id: int | None = None
    summary: str
    detail_a: str | None = None
    detail_b: str | None = None
    status: str
    resolution_note: str | None = None
    detected_by: str
    detected_at: datetime


class ContradictionPatch(BaseModel):
    status: str
    resolution_note: str | None = None


class BehaviorSignalOut(ORMModel):
    id: int
    dealer_id: int
    dimension: str
    code: str
    polarity: int
    weight: float
    interaction_id: int | None = None
    observed_at: datetime | None = None
    detail: str | None = None
    source: str


class NotificationOut(ORMModel):
    id: int
    dealer_id: int | None = None
    type: str
    severity: str
    title: str
    body: str | None = None
    interaction_id: int | None = None
    offer_id: int | None = None
    created_at: datetime
    read_at: datetime | None = None
    dismissed_at: datetime | None = None


class DraftIn(BaseModel):
    dealer_id: int
    contact_id: int | None = None
    channel: Channel = Channel.EMAIL
    in_reply_to_interaction_id: int | None = None
    subject: str | None = None
    body: str
    rationale: str | None = None
    strategy_notes: str | None = None


class DraftPatch(BaseModel):
    subject: str | None = None
    body: str | None = None
    status: str | None = None


class DraftOut(Timestamped):
    id: int
    dealer_id: int
    contact_id: int | None = None
    channel: str
    in_reply_to_interaction_id: int | None = None
    subject: str | None = None
    body: str
    rationale: str | None = None
    strategy_notes: str | None = None
    status: str
    edited_by_user: bool
    generated_by: str
    approved_at: datetime | None = None
    sent_at: datetime | None = None
