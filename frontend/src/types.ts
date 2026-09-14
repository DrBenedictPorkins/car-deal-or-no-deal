/** Mirrors the backend read models. Money is integer cents everywhere. */

export interface DashboardRow {
  dealer_id: number;
  dealer_name: string;
  is_local: boolean;
  distance_miles: number | null;
  primary_contact: string | null;
  primary_contact_role: string | null;
  contact_is_automated: boolean;
  vehicle_summary: string | null;
  vin: string | null;
  selling_price_cents: number | null;
  dealer_controlled_cents: number | null;
  otd_cents: number | null;
  otd_is_written: boolean;
  otd_variance_cents: number | null;
  offer_version: number | null;
  state_code: string;
  state_label: string;
  state_is_pinned: boolean;
  last_interaction_at: string | null;
  last_interaction_channel: string | null;
  last_interaction_direction: string | null;
  idle_hours: number | null;
  owes_response: string;
  owes_reason: string;
  next_action_code: string;
  next_action: string;
  next_action_detail: string;
  next_action_priority: number;
  next_action_severity: string;
  unresolved_issues: string[];
  warnings: string[];
  friction_points: number;
  friction_reasons: string[];
  is_best_otd: boolean;
  gap_to_best_cents: number | null;
}

export interface DashboardSummary {
  dealers_total: number;
  dealers_contacted: number;
  dealers_responded: number;
  dealers_silent: number;
  dealers_with_offer: number;
  best_otd_cents: number | null;
  best_otd_dealer: string | null;
  best_dealer_controlled_cents: number | null;
  best_dealer_controlled_dealer: string | null;
  you_owe_count: number;
  dealer_owes_count: number;
  open_contradictions: number;
  recent_changes: string[];
}

export interface Dashboard {
  summary: DashboardSummary;
  rows: DashboardRow[];
}

export interface OfferLine {
  id: number;
  offer_id: number;
  kind: string;
  name: string;
  price_cents: number;
  is_dealer_controlled: boolean;
  is_taxable: boolean | null;
  mandatory_claimed: boolean | null;
  already_installed: boolean | null;
  user_wants: boolean;
  removable_confirmed: boolean | null;
  notes: string | null;
}

export interface Pricing {
  offer_id: number;
  version: number;
  quoted_at: string;
  msrp_cents: number | null;
  selling_price_cents: number | null;
  /** "SELLING" when the dealer quoted a price, "ADVERTISED" when they only listed one. */
  price_basis: string | null;
  add_ons_total_cents: number;
  dealer_fees_total_cents: number;
  government_total_cents: number;
  dealer_controlled_cents: number | null;
  computed_otd_cents: number | null;
  quoted_otd_cents: number | null;
  effective_otd_cents: number | null;
  otd_variance_cents: number | null;
  otd_reconciled: boolean;
  discount_from_msrp_cents: number | null;
  taxable_base_cents: number | null;
  implied_tax_rate_bp: number | null;
  expected_tax_rate_bp: number | null;
  tax_rate_variance_bp: number | null;
  unwanted_add_ons_cents: number;
  clean_dealer_controlled_cents: number | null;
  clean_otd_cents: number | null;
  fees_disclosed: boolean;
  government_disclosed: boolean;
  warnings: string[];
  is_complete: boolean;
}

export interface Offer {
  id: number;
  dealer_id: number;
  vehicle_id: number | null;
  interaction_id: number | null;
  version: number;
  is_current: boolean;
  quoted_at: string;
  advertised_price_cents: number | null;
  advertised_includes_fees: boolean | null;
  msrp_cents: number | null;
  selling_price_cents: number | null;
  doc_fee_cents: number | null;
  processing_fee_cents: number | null;
  other_taxable_fees_cents: number | null;
  other_non_tax_fees_cents: number | null;
  tax_cents: number | null;
  registration_cents: number | null;
  title_fee_cents: number | null;
  quoted_otd_cents: number | null;
  financing_required: boolean | null;
  financing_provider: string | null;
  apr_bp: number | null;
  financing_term_months: number | null;
  minimum_financed_cents: number | null;
  minimum_loan_months: number | null;
  prepayment_penalty: boolean | null;
  discount_clawback: boolean | null;
  expires_at: string | null;
  deadline_note: string | null;
  notes: string | null;
  lines: OfferLine[];
  pricing: Pricing | null;
}

export interface Fact {
  id: number;
  subject_type: string;
  subject_id: number;
  attribute: string;
  display_value: string | null;
  status: string;
  superseded_by_id: number | null;
  method: string;
  confidence: number | null;
  interaction_id: number | null;
  quote: string | null;
  asserted_by_party: string;
  observed_at: string | null;
  created_at: string;
}

export interface Interaction {
  id: number;
  dealer_id: number | null;
  channel: string;
  direction: string;
  occurred_at: string;
  subject: string | null;
  raw_content: string | null;
  normalized_content: string | null;
  actor_kind: string;
  classification_reason: string | null;
  needs_review: boolean;
}

export interface Provenance {
  fact: Fact;
  interaction: Interaction | null;
  superseded: Fact[];
}

export interface ScoredDimension {
  dimension: string;
  score: number | null;
  reasons: string[];
  basis: string | null;
}

export interface BehaviorReport {
  dealer_id: number;
  transparency: ScoredDimension;
  responsiveness: ScoredDimension;
  price_competitiveness: ScoredDimension;
  friction_points: number;
  friction_reasons: string[];
  friction_label: string;
}

export interface Recommendation {
  code: string;
  headline: string;
  detail: string;
  priority: number;
  severity: string;
  supporting_numbers: Record<string, string>;
  suggested_message: string | null;
}

export interface TimelineEvent {
  kind: string;
  id: number;
  at: string;
  channel?: string;
  direction?: string;
  actor_kind?: string;
  subject?: string | null;
  preview?: string;
  version?: number;
  otd_cents?: number | null;
  dealer_controlled_cents?: number | null;
  warnings?: string[];
  needs_review?: boolean;
}

export interface OfferDelta {
  from_version: number;
  to_version: number;
  selling_price_delta_cents: number | null;
  dealer_controlled_delta_cents: number | null;
  otd_delta_cents: number | null;
  add_ons_delta_cents: number;
  added_lines: string[];
  removed_lines: string[];
  improved: boolean;
}

export interface Contradiction {
  id: number;
  dealer_id: number;
  attribute: string | null;
  kind: string;
  severity: string;
  summary: string;
  detail_a: string | null;
  detail_b: string | null;
  status: string;
  detected_at: string;
}

export interface Question {
  id: number;
  dealer_id: number;
  asked_by: string;
  text: string;
  topic: string | null;
  importance: string;
  status: string;
  asked_at: string | null;
}

export interface Commitment {
  id: number;
  dealer_id: number;
  party: string;
  text: string;
  due_at: string | null;
  status: string;
}

export interface Draft {
  id: number;
  dealer_id: number;
  subject: string | null;
  body: string;
  rationale: string | null;
  strategy_notes: string | null;
  status: string;
  edited_by_user: boolean;
  created_at: string | null;
}

export interface Dealer {
  id: number;
  name: string;
  city: string | null;
  state: string | null;
  website: string | null;
  phone: string | null;
  distance_miles: number | null;
  is_local: boolean;
  state_code: string;
  state_is_pinned: boolean;
  status: string;
  notes: string | null;
}

export interface Contact {
  id: number;
  dealer_id: number;
  name: string | null;
  title: string | null;
  email: string | null;
  phone: string | null;
  role: string;
  actor_kind: string;
  automation_evidence: string | null;
  is_primary: boolean;
}

export interface Vehicle {
  id: number;
  year: number | null;
  make: string | null;
  model: string | null;
  trim: string | null;
  vin: string | null;
  exterior_color: string | null;
  mileage: number | null;
  msrp_cents: number | null;
  is_demo: boolean | null;
  is_loaner: boolean | null;
  damage_history: string | null;
}

export interface StateTransition {
  id: number;
  from_state: string | null;
  to_state: string;
  reason_text: string | null;
  triggered_by: string;
  was_suppressed_by_pin: boolean;
  created_at: string;
}

export interface BehaviorSignal {
  id: number;
  dimension: string;
  code: string;
  polarity: number;
  weight: number;
  detail: string | null;
  observed_at: string | null;
}

export interface DealerDetail {
  dealer: Dealer;
  state_label: string;
  contacts: Contact[];
  vehicles: Vehicle[];
  current_offer: Offer | null;
  offer_history: Offer[];
  offer_progression: OfferDelta[];
  timeline: TimelineEvent[];
  questions: Question[];
  commitments: Commitment[];
  contradictions: Contradiction[];
  facts: Fact[];
  signals: BehaviorSignal[];
  behavior: BehaviorReport;
  recommendation: Recommendation;
  drafts: Draft[];
  transitions: StateTransition[];
  owes_response: string;
  owes_reason: string;
  idle_hours: number | null;
  summary: string;
}

export interface ComparisonRow {
  dealer_id: number;
  dealer_name: string;
  is_local: boolean;
  distance_miles: number | null;
  state_code: string;
  vehicle_summary: string | null;
  vin: string | null;
  exterior_color: string | null;
  mileage: number | null;
  is_demo: boolean | null;
  msrp_cents: number | null;
  selling_price_cents: number | null;
  discount_from_msrp_cents: number | null;
  dealer_fees_total_cents: number | null;
  fees_disclosed: boolean;
  add_ons_total_cents: number | null;
  dealer_controlled_cents: number | null;
  clean_dealer_controlled_cents: number | null;
  tax_cents: number | null;
  registration_cents: number | null;
  government_total_cents: number | null;
  otd_cents: number | null;
  otd_variance_cents: number | null;
  financing_required: boolean | null;
  financing_provider: string | null;
  apr_bp: number | null;
  financing_term_months: number | null;
  friction_points: number;
  friction_reasons: string[];
  unresolved_issues: string[];
  cleanliness_score: number;
  cleanliness_reasons: string[];
  offer_version: number | null;
  quoted_at: string | null;
}

export interface Winner {
  label: string;
  dealer_id: number | null;
  dealer_name: string | null;
  value: string;
  explanation: string;
}

export interface Comparison {
  rows: ComparisonRow[];
  winners: Winner[];
  notes: string[];
}

export interface Notification {
  id: number;
  dealer_id: number | null;
  type: string;
  severity: string;
  title: string;
  body: string | null;
  created_at: string;
}

export interface BuyerProfile {
  id: number;
  display_name: string | null;
  email: string | null;
  phone: string | null;
  purchase_type: string;
  target_year: number | null;
  target_make: string | null;
  target_model: string | null;
  target_trim: string | null;
  target_powertrain: string | null;
  color_preferences: string | null;
  excluded_colors: string | null;
  has_trade_in: boolean;
  cash_available: boolean;
  financing_acceptable: string;
  wants_add_ons: boolean;
  wants_maintenance_plan: boolean;
  registration_state: string | null;
  zip_code: string | null;
  expected_tax_rate_bp: number | null;
  max_distance_miles: number | null;
  local_dealer_premium_cents: number;
  contact_preference: string;
  avoid_phone_calls: boolean;
  avoid_dealership_visits: boolean;
  follow_up_after_hours: number;
  no_response_after_days: number;
  notes: string | null;
}

export interface StateDef {
  code: string;
  label: string;
  description: string | null;
  sort_order: number;
  is_terminal: boolean;
  is_active_pipeline: boolean;
  is_builtin: boolean;
}

export interface Health {
  status: string;
  llm_enabled: boolean;
  llm_provider: string;
  data_dir: string;
}

export interface Answer {
  question: string;
  answer: string;
  rows: Record<string, unknown>[];
}
