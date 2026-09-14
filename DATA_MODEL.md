# Data Model

SQLite via SQLAlchemy 2.0, migrated with Alembic. Design rules for this schema:

1. **Money is integer cents.** Column names end in `_cents`. No floats in the money path.
   The API boundary converts to/from decimal strings exactly once.
2. **No JSON blobs for anything queryable.** JSON is used only for genuinely
   free-shaped payloads (notification context, raw extractor output kept for audit).
3. **Enumerations that describe workflow are data** (catalog tables), because the user
   explicitly wants states to evolve. Enumerations that describe physics (channel,
   direction) are Python enums stored as strings with a CHECK constraint.
4. **History is never overwritten.** Offers version, facts supersede, state transitions
   append. Nothing meaningful is destroyed by an update.
5. **Everything derived is recomputed**, not trusted from storage, except where a
   materialized column exists purely as an index — and those are covered by a consistency
   test.

Timestamps are stored UTC. `*_at` = system time; `occurred_at` / `timestamp` = the time
the real-world event happened (which for backfilled email is much earlier than ingest).

---

## Entity map

```
BuyerProfile (1)
   │
Campaign (1) ─────────────────────────────────────────────────┐
   │                                                          │
   ├── Dealer ──┬── Contact ──────────────┐                   │
   │            ├── Vehicle ──────┐       │                   │
   │            ├── StateTransition│      │                   │
   │            ├── BehaviorSignal │      │                   │
   │            └── Interaction ◄──┴───────┘  (dealer, contact, vehicle)
   │                   │
   │                   ├── EmailSource (1:1, channel=EMAIL)
   │                   ├── TranscriptSource (1:1, channel=CALL)
   │                   ├── Document (0:N)
   │                   ├── Offer (0:N) ── OfferLine (add-ons & fees)
   │                   ├── Fact (0:N, provenance edge)
   │                   ├── Question (asked / answered)
   │                   └── Commitment
   │
   ├── Contradiction (two facts / two interactions)
   ├── DraftMessage
   ├── Notification
   └── LLMRun (audit of every model call)
```

---

## Core reference tables

### `negotiation_state` — the state catalog (data, not an enum)

| column | type | notes |
| --- | --- | --- |
| `code` | text PK | `DISCOVERED`, `CONTACTED`, … |
| `label` | text | display name |
| `description` | text | what it means |
| `sort_order` | int | pipeline ordering for the board |
| `is_terminal` | bool | `ACCEPTED`, `LOST`, `CLOSED` |
| `is_active_pipeline` | bool | counts toward "live negotiations" |
| `is_builtin` | bool | user-added states are `false` |

Seeded with: `DISCOVERED, CONTACTED, AWAITING_RESPONSE, RESPONDED, AWAITING_QUOTE,
QUOTE_RECEIVED, COUNTER_SENT, AWAITING_COUNTER, FINALIST, ACCEPTED, LOST, CLOSED,
NO_RESPONSE`. Adding a state is an INSERT, not a migration.

### Hard enums (string + CHECK)

| enum | values |
| --- | --- |
| `Channel` | `EMAIL, CALL, SMS, IN_PERSON, NOTE, DOCUMENT` |
| `Direction` | `INBOUND, OUTBOUND, INTERNAL` |
| `ActorKind` | `HUMAN, AUTOMATED, UNKNOWN` |
| `Party` | `BUYER, DEALER, NOBODY` |
| `FactStatus` | `CANDIDATE, CURRENT, SUPERSEDED, DISPUTED, RETRACTED` |
| `ExtractionMethod` | `MANUAL, RULE, LLM, IMPORT` |
| `SubjectType` | `DEALER, CONTACT, VEHICLE, OFFER, DEAL` |
| `FeeKind` | `ADD_ON, DEALER_FEE, GOVERNMENT, OTHER` |
| `DraftStatus` | `DRAFT, APPROVED, DISCARDED, SENT, FAILED` |
| `BehaviorDimension` | `TRANSPARENCY, FRICTION, RESPONSIVENESS, PRICE_COMPETITIVENESS` |

---

## `buyer_profile`

Single logical row (`id=1`) describing the buyer; consumed by the recommender, the
drafting prompts, and the tax checks.

| column | type | notes |
| --- | --- | --- |
| `id` | int PK | |
| `display_name`, `email`, `phone` | text | used to detect the buyer's own messages |
| `purchase_type` | text | `NEW_ONLY`, `NEW_OR_USED`, `USED_ONLY` |
| `target_year/make/model/trim` | text | e.g. 2026 / Honda / Civic Hatchback / Sport |
| `target_powertrain` | text | `NON_HYBRID` in the reference scenario |
| `color_preferences` | text | free text, e.g. "dark/subdued" |
| `excluded_colors` | text | comma list, e.g. "white" |
| `has_trade_in` | bool | |
| `cash_available` | bool | |
| `financing_acceptable` | text | `NEVER`, `ONLY_IF_ADVANTAGEOUS`, `PREFERRED` |
| `wants_add_ons` / `wants_maintenance_plan` | bool | both false in the reference case |
| `registration_state` | text | `PA` |
| `zip_code` | text | `18435` |
| `expected_tax_rate_bp` | int | basis points — `600` = 6.00%. Integer, not float |
| `max_distance_miles` | int | nullable |
| `local_dealer_premium_cents` | int | how much extra a local dealer is worth |
| `contact_preference` | text | `EMAIL_PREFERRED`, … |
| `avoid_phone_calls`, `avoid_dealership_visits` | bool | |
| `notes` | text | |

`expected_tax_rate_bp` in basis points keeps the tax check in exact integer arithmetic.

## `campaign`

One shopping effort. Nullable FK everywhere it appears in Phase 1 (assumption **A2**),
so multi-campaign support later is additive rather than a rewrite.

`id, name, target_description, status, opened_at, closed_at, notes`

---

## `dealer`

| column | type | notes |
| --- | --- | --- |
| `id` | int PK | |
| `campaign_id` | int FK null | |
| `name` | text | "Honda of Stamford" |
| `address, city, state, postal_code` | text | |
| `website, phone` | text | |
| `email_domains` | text | comma-separated hint for resolution (**A3**) |
| `distance_miles` | numeric null | |
| `is_local` | bool | user-asserted convenience flag |
| `state_code` | text FK → `negotiation_state.code` | current negotiation state |
| `state_is_pinned` | bool | user override; engine logs what it *would* have done |
| `state_changed_at` | datetime | |
| `first_contacted_at` | datetime null | |
| `status` | text | lifecycle: `ACTIVE`, `ARCHIVED` (distinct from negotiation state) |
| `notes` | text | |
| `created_at, updated_at` | datetime | |

Deliberately **not** stored: current price, OTD, who owes a response, idle days, next
action, friction score. All are derived by services on read — storing them creates two
sources of truth and they would go stale on every ingest.

## `contact`

`id, dealer_id, name, title, email (unique-ish index), phone, role, actor_kind,
automation_evidence, is_primary, notes, created_at, updated_at`

- `role`: `SALESPERSON, SALES_MANAGER, INTERNET_SALES, BDC, CLIENT_SERVICES,
  FINANCE_MANAGER, GENERAL_MANAGER, AUTOMATED_ASSISTANT, UNKNOWN`
- `actor_kind`: `HUMAN | AUTOMATED | UNKNOWN` — a contact-level rollup.
- `automation_evidence`: text explaining *why* we think a contact is automated
  (e.g. "3 of 4 messages restate the buyer's original inquiry verbatim; sent via
  `@leadmanager.example`"). A classification without a stated reason is not acceptable.

Classification is also per-interaction (`interaction.actor_kind`), because the same
"Sales Manager" address emits both automated blasts and genuine replies. The contact-level
value is a rollup, not a substitute.

## `vehicle`

`id, dealer_id, campaign_id, year, make, model, trim, body_style, vin, stock_number,
exterior_color, interior_color, mileage, msrp_cents, condition (NEW|USED|CERTIFIED),
is_demo, is_loaner, damage_history, availability, notes, created_at, updated_at`

`is_demo` / `is_loaner` are **nullable booleans**: `NULL` = never asked, `false` = dealer
affirmatively said no. Those are different negotiation facts and collapsing them loses
information. Unique index on `vin` where not null.

---

## `interaction` — the fundamental communication object

| column | type | notes |
| --- | --- | --- |
| `id` | int PK | |
| `dealer_id, contact_id, vehicle_id` | FK null | resolution may be pending |
| `campaign_id` | FK null | |
| `channel` | Channel | |
| `direction` | Direction | |
| `occurred_at` | datetime | when it actually happened |
| `subject` | text | |
| `raw_content` | text | exactly as received |
| `normalized_content` | text | HTML→text, quotes/signature stripped |
| `quoted_content` | text | what was stripped — preserved, never discarded (**A5**) |
| `summary` | text null | LLM, Phase 3 |
| `actor_kind` | ActorKind | per-message human/automated |
| `classification_reason` | text | why |
| `is_quote_bearing` | bool | contains pricing worth extracting |
| `source_system` | text | `gmail`, `upload`, `manual` |
| `source_identifier` | text | Gmail message id / file hash |
| `source_thread_identifier` | text | Gmail thread id |
| `source_file` | text | blob path |
| `content_hash` | text | sha256 of raw_content |
| `needs_review` | bool | unresolved dealer, low-confidence extraction |
| `extractor_version` | text null | which extraction pass has run |
| `created_at` | datetime | ingest time |

Unique: `(source_system, source_identifier)` and `content_hash` — the two dedupe guards.
Index: `(dealer_id, occurred_at)`, `(source_thread_identifier)`, `(needs_review)`.

### `email_source` (1:1 where `channel = EMAIL`)

`interaction_id PK, provider, provider_message_id, provider_thread_id, rfc822_message_id,
in_reply_to, references, from_name, from_email, to_emails, cc_emails, sent_at,
snippet, label_ids, has_attachments, mime_summary, history_id`

Dedupe uses `provider_message_id` **and** `rfc822_message_id`, because the same message
reaches the mailbox twice under different Gmail ids in forwarding setups.

### `transcript_source` (1:1 where `channel = CALL`)

`interaction_id PK, format (TXT|MD|SRT|VTT|AUDIO), original_filename, duration_seconds,
speaker_count, transcription_engine, transcription_confidence, audio_blob_path,
diarization_available`

### `transcript_segment`

`id, interaction_id, index, start_ms, end_ms, speaker_label, speaker_party (BUYER|DEALER|
UNKNOWN), text`

Segments give facts extracted from a call a **timecoded** citation — the contradiction
example ("phone call 2:14 PM") needs to point at a moment, not a whole file.

### `document`

`id, interaction_id null, dealer_id null, offer_id null, kind (PDF|IMAGE|TEXT|EML|OTHER),
original_filename, mime_type, byte_size, sha256, blob_path, page_count,
extracted_text, extraction_engine, extraction_confidence, needs_review, created_at`

The original file is always retained; `extracted_text` is derived and regenerable.

---

## `offer` — versioned, never replaced

| column | type | notes |
| --- | --- | --- |
| `id` | int PK | |
| `dealer_id, vehicle_id, interaction_id, campaign_id` | FK | `interaction_id` is the provenance |
| `version` | int | per-dealer sequence, 1, 2, 3… |
| `supersedes_offer_id` | FK null | explicit chain |
| `is_current` | bool | exactly one true per (dealer, vehicle) — enforced in service + partial index |
| `quoted_at` | datetime | when the dealer made it |
| `msrp_cents` | int null | |
| `advertised_price_cents` | int null | |
| `selling_price_cents` | int null | |
| `discount_cents` | int null | stored when *stated*; also derivable as msrp − selling |
| `destination_cents` | int null | |
| `doc_fee_cents` | int null | |
| `processing_fee_cents` | int null | |
| `other_taxable_fees_cents` | int null | dealer-controlled by default (**A1**) |
| `other_non_tax_fees_cents` | int null | government pass-through by default (**A1**) |
| `tax_cents` | int null | |
| `registration_cents` | int null | |
| `title_fee_cents` | int null | |
| `quoted_otd_cents` | int null | what the dealer *said* the OTD is |
| `financing_required` | bool null | tri-state |
| `financing_provider` | text null | |
| `apr_bp` | int null | basis points — `714` = 7.14% |
| `financing_term_months` | int null | |
| `minimum_financed_cents` | int null | |
| `minimum_loan_months` | int null | |
| `prepayment_penalty` | bool null | **NULL = unconfirmed, and that matters** |
| `discount_clawback` | bool null | same |
| `clawback_terms` | text null | |
| `expires_at` | datetime null | |
| `deadline_note` | text null | "need to know by tonight" |
| `confidence` | text | `HIGH/MEDIUM/LOW` from extraction |
| `needs_review` | bool | |
| `notes` | text | |
| `created_at` | datetime | |

`add_ons_total` is **not** a column — it is `SUM(offer_line)` where the line is an add-on.
Storing a total that can disagree with its parts is the exact failure this system exists
to prevent.

### `offer_line` — add-ons and itemized fees

| column | type | notes |
| --- | --- | --- |
| `id, offer_id` | | |
| `kind` | FeeKind | `ADD_ON \| DEALER_FEE \| GOVERNMENT \| OTHER` |
| `name` | text | "VIN Etching", "Wheel Locks" |
| `price_cents` | int | |
| `is_dealer_controlled` | bool | drives normalization; overridable (**A1**) |
| `is_taxable` | bool null | feeds the implied-tax-rate check |
| `mandatory_claimed` | bool null | dealer *says* it is mandatory |
| `already_installed` | bool null | "it's already on the car" |
| `user_wants` | bool | defaults from buyer profile |
| `removable_confirmed` | bool null | dealer confirmed it can come off |
| `notes` | text | |

### Derived (computed in `pricing.py`, never stored as truth)

```
add_ons_total          = Σ price where kind = ADD_ON
dealer_fees_total      = doc_fee + processing_fee + other_taxable_fees
                         + Σ price where kind = DEALER_FEE
government_total       = tax + registration + title + other_non_tax_fees
                         + Σ price where kind = GOVERNMENT
dealer_controlled      = selling_price + dealer_fees_total + add_ons_total
computed_otd           = dealer_controlled + government_total
otd_variance           = quoted_otd − computed_otd        → flag when ≠ 0
implied_tax_rate_bp    = round(tax / taxable_base × 10000)
tax_rate_variance_bp   = implied − profile.expected_tax_rate_bp → flag
clean_dealer_controlled= dealer_controlled − Σ unwanted removable add-ons
discount_from_msrp     = msrp − selling_price
```

Worked against the real data:

| | Westport | Stamford v1 | Stamford v2 |
| --- | ---: | ---: | ---: |
| selling | 28,035.00 | 28,090.00 | 27,329.42 |
| dealer fees + add-ons | 699.00 | 1,097.00 | 1,097.00 |
| **dealer-controlled** | **28,734.00** | **29,187.00** | **28,426.42** |
| government | 1,941.10 | 2,119.22 | 2,073.59 |
| computed OTD | 30,675.10 | 31,306.22 | 30,500.01 |
| quoted OTD | 30,775.10 | 31,306.22 | 30,500.01 |
| **OTD variance** | **+100.00 ⚠** | 0.00 | 0.00 |

The $100 Westport gap falls out of the schema automatically. That is the point.

---

## `fact` — provenance for everything the system believes

| column | type | notes |
| --- | --- | --- |
| `id` | int PK | |
| `subject_type, subject_id` | SubjectType, int | `VEHICLE:7` |
| `attribute` | text | dotted key, e.g. `vehicle.is_demo` |
| `value_text, value_number, value_bool, value_datetime` | typed columns | one populated |
| `value_unit` | text null | `cents`, `miles`, `bp` |
| `display_value` | text | rendered form for the UI |
| `status` | FactStatus | CANDIDATE → CURRENT → SUPERSEDED/DISPUTED/RETRACTED |
| `superseded_by_id` | FK null | append-only chain |
| `method` | ExtractionMethod | MANUAL / RULE / LLM / IMPORT |
| `confidence` | numeric null | 0–1 for LLM |
| `interaction_id` | FK null | **the source** |
| `document_id, transcript_segment_id, offer_id` | FK null | finer-grained source |
| `quote` | text | verbatim supporting snippet |
| `quote_start, quote_end` | int null | offsets into `normalized_content` |
| `asserted_by_party` | Party | who claimed it |
| `llm_run_id` | FK null | full model-call audit |
| `observed_at` | datetime | when the source said it |
| `created_at` | datetime | when we recorded it |

Index: `(subject_type, subject_id, attribute, status)`.

Never `UPDATE`d except to set `status`/`superseded_by_id`. "What did Stamford originally
quote?" is a query over superseded facts, which is why they cannot be deleted.

## `contradiction`

`id, dealer_id, attribute, fact_a_id, fact_b_id, interaction_a_id, interaction_b_id,
kind (VALUE_CONFLICT | NUMERIC_CONFLICT | PRESENCE_CONFLICT | PROMISE_BROKEN),
severity (INFO|WARNING|CRITICAL), summary, detail, status (OPEN|ACKNOWLEDGED|RESOLVED|
DISMISSED), resolution_note, detected_by (RULE|LLM), detected_at`

Both sides are always retained and displayed. The system **never decides which statement
is true** — it shows the two sources and their timestamps.

## `question` / `commitment`

`question`: `id, dealer_id, interaction_id (asked in), asked_by (BUYER|DEALER), text,
topic, status (OPEN|ANSWERED|IGNORED|WITHDRAWN), answered_interaction_id, answer_summary,
asked_at, answered_at, importance`

`commitment`: `id, dealer_id, party, interaction_id, text, due_at, status (OPEN|KEPT|
BROKEN|EXPIRED|WAIVED), evidence_interaction_id, created_at` — "I'll send the OTD
tonight" becomes an object with a due time, so a broken promise is a
`PROMISE_BROKEN` contradiction rather than a forgotten detail.

## `state_transition`

`id, dealer_id, from_state, to_state, reason_code, reason_text, triggered_by (RULE|USER|
INGEST), interaction_id, rule_id, was_suppressed_by_pin, created_at`

Append-only. The dealer's current state is a cache of the latest row.

## `behavior_signal`

`id, dealer_id, dimension, code, polarity (+1|−1), weight, interaction_id, observed_at,
detail, source (RULE|USER|LLM)`

Codes are a closed vocabulary: `REFUSED_WRITTEN_QUOTE`, `INSISTS_ON_PHONE_CALL`,
`INSISTS_ON_VISIT`, `REPEATED_PHONE_REQUEST`, `WITHHELD_OTD`, `CHANGED_VEHICLE`,
`INTRODUCED_ADD_ON`, `FINANCING_CONDITIONED_PRICE`, `UNEXPLAINED_FEE`, `SLOW_RESPONSE`,
`AUTOMATED_ONLY_CONTACT` / `GAVE_ITEMIZED_OTD`, `ANSWERED_DIRECT_QUESTION`,
`PROVIDED_VIN`, `CONFIRMED_MILEAGE`, `CONFIRMED_DEMO_STATUS`,
`CONFIRMED_FINANCING_TERMS`, `WRITTEN_FINAL_PRICE`, `FAST_RESPONSE`, `PROACTIVE_UPDATE`.

Scores are a deterministic weighted sum of signals. The UI shows the score **and the
signal list that produced it**. There is no opaque AI score anywhere in the product.

## `draft_message`

`id, dealer_id, contact_id, channel, in_reply_to_interaction_id, subject, body, rationale,
strategy_notes, status (DRAFT|APPROVED|DISCARDED|SENT|FAILED), edited_by_user,
llm_run_id, approved_at, sent_at, sent_interaction_id, created_at, updated_at`

Phase 1 exposes EDIT / APPROVE / DISCARD. `SENT` exists but nothing writes it: there is no
send transport, and the Gmail send scope is not requested.

## `notification`

`id, dealer_id null, type, severity, title, body, dedupe_key (unique), context_json,
interaction_id, offer_id, created_at, read_at, dismissed_at`

Types: `NEW_OFFER`, `DEALER_RESPONDED`, `NO_RESPONSE_24H`, `NEW_BEST_OFFER`,
`PRICE_CHANGED`, `NEW_ADD_ON`, `DEADLINE_APPROACHING`, `CONTRADICTION_DETECTED`,
`NEEDS_REVIEW`. `dedupe_key` plus a per-type cooldown implements "do not over-notify" as a
constraint rather than a hope.

## `llm_run`

`id, provider, model, purpose, interaction_id, dealer_id, prompt_hash, schema_name,
input_tokens, output_tokens, latency_ms, status, error, prompt_text null,
response_text null, created_at`

Payload columns are populated only when `LLM_STORE_PAYLOADS=true` (default off).

## `sync_state`

`id, provider, account_email, last_history_id, last_full_sync_at, last_incremental_at,
status, error, cursor_json`

---

## How the required questions are answered

| Question | Query shape |
| --- | --- |
| What is our best offer? | `ORDER BY computed dealer_controlled / OTD` over current offers |
| Who hasn't responded? | dealers with no INBOUND interaction |
| Who owes me a response? | derived `owes_response = DEALER` |
| Who's trying to get me on the phone? | `behavior_signal.code IN (INSISTS_ON_PHONE_CALL, REPEATED_PHONE_REQUEST)` |
| Who gave an actual written OTD? | offers with `quoted_otd_cents NOT NULL` and `otd_variance = 0` from an EMAIL/DOCUMENT interaction |
| Lowest selling price? | `MIN(selling_price_cents)` over current offers |
| What did Stamford originally quote? | `offer WHERE dealer=… ORDER BY version ASC LIMIT 1` |
| Did anyone confirm it wasn't a demo? | `fact WHERE attribute='vehicle.is_demo' AND value_bool = 0` |
| Who added VIN etching? | `offer_line WHERE kind='ADD_ON' AND name LIKE '%etch%'` |
| What changed since yesterday? | `fact`, `offer`, `state_transition`, `interaction` by `created_at` |

Every one is exact SQL over structured columns. None requires re-reading an email, and
none requires asking a model.
