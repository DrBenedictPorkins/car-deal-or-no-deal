# Architecture

**Working name:** `dealbench` — a local-first dealership negotiation state engine.

This document describes the system's shape, its boundaries, and — importantly — the
assumptions it makes that could be wrong. See [DATA_MODEL.md](DATA_MODEL.md) for the
schema and [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for phasing.

---

## 1. What this system is

It is **not** an email client. It is a **state engine** that maintains a structured,
provenance-backed model of an in-flight multi-dealer car negotiation. Email, phone
transcripts, and documents are *input channels* that feed the engine. The dashboard is a
*projection* of engine state.

The engine answers two questions on sight:

1. Where do I stand with every dealership?
2. What should I do next?

### 1.1 The central inversion

Most "AI email" tools treat the LLM as the memory: you re-read the thread and ask the
model what's going on. That is unreliable, unauditable, and unqueryable.

Here, the LLM is a **sensor and a writer**, never the record:

| LLM does | Application code does |
| --- | --- |
| Classify (human vs. automated, channel intent) | All arithmetic |
| Extract structured candidate facts from text | Price normalization and comparison |
| Summarize a thread | State transitions |
| Propose the *narrative* of a next action | Deriving "who owes a response" |
| Draft a reply | Deduplication, idempotency |
| Spot *candidate* contradictions | Confirming contradictions numerically |

**Hard rule: the LLM never performs arithmetic that application code can perform.**
Extraction returns *line items*; Python computes totals, deltas, tax bases, and rankings.
If the model returns a total, we recompute it and treat a mismatch as a data-quality
flag, not as truth.

---

## 2. Layering

```
┌───────────────────────────────────────────────────────────────────────┐
│ UI — React SPA (Vite + TS)                                            │
│   Dashboard · Dealer Detail · Comparison · Profile · Inbox/Review     │
└───────────────────────────▲───────────────────────────────────────────┘
                            │ JSON over HTTP (localhost only)
┌───────────────────────────┴───────────────────────────────────────────┐
│ API — FastAPI routers (thin: validate, call service, serialize)       │
├───────────────────────────────────────────────────────────────────────┤
│ DOMAIN SERVICES (deterministic, no I/O to LLM or network)             │
│   pricing.py        normalization, OTD reconciliation, deltas         │
│   state_engine.py   negotiation state rules + transition log          │
│   comparison.py     multi-dealer apples-to-apples ranking             │
│   behavior.py       friction/transparency signals → explained scores  │
│   contradictions.py numeric + categorical conflict confirmation       │
│   recommender.py    rule-based next action (LLM refines, later)       │
│   facts.py          append-only fact write + supersede + provenance   │
│   notifications.py  event emission with dedupe                        │
├───────────────────────────────────────────────────────────────────────┤
│ INGESTION (channel adapters → canonical Interaction)                  │
│   gmail/      OAuth, history sync, MIME → normalized text             │
│   transcript/ txt · md · srt · vtt (+ audio upload, transcribe later) │
│   document/   pdf · image · text (+ OCR later)                        │
│   manual/     typed notes, pasted email, manual offers                │
├───────────────────────────────────────────────────────────────────────┤
│ ENRICHMENT (the only LLM consumers)                                   │
│   classify · extract · summarize · draft                              │
│   every call recorded as an LLMRun; every output is a *candidate*     │
├───────────────────────────────────────────────────────────────────────┤
│ PERSISTENCE — SQLAlchemy 2.0 → SQLite (WAL). Alembic migrations.      │
│   Blobs (raw .eml, PDFs, audio) on local disk, hashed, referenced.    │
└───────────────────────────────────────────────────────────────────────┘
```

### 2.1 Dependency direction

`api → services → repositories → models`. Services never import routers. Ingestion and
enrichment depend on services, never the reverse. The LLM package is imported **only** by
`enrichment/`; no domain service may import an LLM client. This is enforced by a test
(`test_layering.py`) that greps the import graph — it is easy to violate accidentally and
it is the single most important structural invariant in the codebase.

---

## 3. The pipeline

Every input, regardless of channel, follows the same path:

```
   raw source (Gmail msg | transcript file | PDF | typed note)
        │
        ▼  [1] ACQUIRE — fetch/upload, store raw blob, compute content hash
   RawSource (immutable, on disk + row)
        │
        ▼  [2] NORMALIZE — MIME/HTML→text, strip quoted replies & signatures
   Interaction (channel, direction, timestamp, raw_content, normalized_content)
        │
        ▼  [3] RESOLVE — attach to Dealer / Contact / Vehicle
   identity resolution (deterministic first, LLM only to break ties)
        │
        ▼  [4] CLASSIFY — human/automated, intent, is_quote_bearing
   ContactClassification, interaction flags
        │
        ▼  [5] EXTRACT — schema-constrained LLM → candidate facts + offer lines
   Fact(status=CANDIDATE) · Offer(draft) · Question · Commitment
        │
        ▼  [6] RECONCILE — deterministic: compute totals, compare to quoted,
        │                  supersede prior facts, detect contradictions
   Offer(computed columns) · Fact(CURRENT/SUPERSEDED) · Contradiction
        │
        ▼  [7] TRANSITION — state engine evaluates rules, writes transition log
   Dealer.state · StateTransition · Notification
        │
        ▼  [8] PROJECT — dashboard rows, comparison, recommendations
```

Steps 1–4 and 6–8 are deterministic and independently testable. Step 5 is the only
non-deterministic step, and its output is quarantined as *candidate* data until step 6
either accepts it or flags it for review.

### 3.1 Idempotency

Re-running the pipeline on the same source must not create duplicates. Each stage is keyed:

- **Acquire** — unique on `(provider, provider_message_id)` for email;
  `sha256(content)` for files.
- **Extract** — unique on `(interaction_id, extractor_version)`; re-extraction with a
  newer version supersedes rather than duplicates.
- **Facts** — a new fact with the same `(subject, attribute, value)` from the same source
  is a no-op; a different value supersedes and preserves history.
- **Notifications** — `dedupe_key` with a suppression window.

---

## 4. Provenance model

> "Why does the system believe this?" must always be answerable.

Every non-user-entered assertion is a `Fact` row carrying:

- the **subject** it describes (`dealer:3`, `vehicle:7`, `offer:12`)
- the **attribute** (`vehicle.is_demo`, `offer.financing_required`)
- a typed **value**
- the **source** — `interaction_id` and/or `document_id`
- the **verbatim quote** plus character offsets into `normalized_content`
- the **method** (`MANUAL` | `RULE` | `LLM`) and, for LLM, the `llm_run_id`
  (provider, model, prompt hash, timestamp)
- **lifecycle**: `CANDIDATE → CURRENT → SUPERSEDED | DISPUTED | RETRACTED`

Facts are **append-only**. Nothing is updated in place; a change writes a new row and
points the old one at it via `superseded_by_id`. The UI's fact chip is clickable and opens
the exact source interaction scrolled to the quoted span.

Structured tables (`Offer`, `Vehicle`) carry denormalized convenience columns for fast
querying, but each such column has a corresponding Fact establishing it. The Fact is the
audit record; the column is the index. A consistency test asserts they don't diverge.

---

## 5. Pricing and normalization

Money is stored as **integer cents**. No floats anywhere in the money path. The API
speaks decimal strings; conversion happens at the schema boundary exactly once.

Two headline numbers, always shown together:

```
DEALER-CONTROLLED COST = selling_price
                       + doc_fee + processing_fee + other_taxable_fees
                       + add_ons_total
                       (government charges excluded)

OTD = dealer_controlled + tax + registration + title + other_non_tax_fees
```

Rationale: tax and registration vary by the buyer's jurisdiction and are not negotiable,
so including them lets a dealer in a low-tax state look cheaper than one who is actually
offering a better car deal. Dealer-controlled cost is the number the dealer can move.

Three derived integrity checks run on every offer, in code, with no LLM involvement:

1. **OTD reconciliation** — does the sum of line items equal the dealer's quoted OTD? A
   gap means there is an unexplained charge. *(The real Westport offer has a $100 gap —
   this is a fixture, not a hypothetical.)*
2. **Implied tax rate** — `tax / taxable_base`, compared against the buyer profile's
   expected rate. A mismatch means either an undisclosed taxable item or a wrong tax base.
3. **Clean-offer variant** — the OTD recomputed with add-ons the buyer does not want
   removed, so "what would this be if they dropped the VIN etching?" is a column, not a
   conversation.

Every comparison is computed from these; nothing is ranked by model output.

---

## 6. Negotiation state

State lives on the dealer, but the **set of states is data, not an enum in code**. A
`negotiation_state` catalog table is seeded with the default states and can be extended
without a migration. Transitions are produced by a declarative rule set evaluated in
priority order (`state_engine.py`), each rule returning a target state plus a
human-readable reason. Every transition is written to an append-only `StateTransition`
log with the triggering interaction.

Derived, never stored as opinion:

- `owes_response` — computed from last inbound vs. last outbound plus open questions
- `idle_days` — from the newer of the two
- `next_action` — rule-based in Phase 1; LLM *narrates* the rule's conclusion in Phase 3,
  it does not choose it

A user may pin a state manually. Manual pins are respected and the engine records that it
*would* have moved, rather than silently overriding the human.

---

## 7. Channel adapters

The `Interaction` is the fundamental communication object; email is one channel among
several. An adapter's only job is `raw source → Interaction + channel-specific side table`.

| Channel | Adapter | Side table | Phase |
| --- | --- | --- | --- |
| EMAIL | Gmail API (OAuth 2.0, incremental `historyId`) | `email_source` | 2 |
| CALL | txt/md/srt/vtt parser; audio upload → transcription later | `transcript_source` | 4 |
| DOCUMENT | PDF/image/text extraction | `document` | 4 |
| NOTE / IN_PERSON / SMS | manual entry | — | 1 |

Adding a provider (Outlook, IMAP) means writing an adapter, not touching the engine.

### 7.1 Gmail specifics

- OAuth 2.0 installed-app flow with loopback redirect. **No password is ever requested or
  stored.** Scope is `gmail.readonly` in Phases 2–4; `gmail.compose` is added only when
  draft-push is switched on, and `gmail.send` is never requested in the initial design.
- Historical import by query (`from:` domains, label, or date window), then incremental
  sync via `users.history.list` from a stored `historyId`, falling back to a bounded
  date-window scan if the history cursor expires.
- Dedupe on Gmail `message_id` **and** RFC-822 `Message-ID`.
- HTML is converted to text; quoted reply chains and signature blocks are detected and
  marked so extraction sees only the new content — an automated lead-management system
  that quotes the buyer's original inquiry verbatim must not be read as the dealer
  *agreeing* to those terms. This is a real failure mode from the source workflow.

---

## 8. LLM provider abstraction

```python
class LLMProvider(Protocol):
    name: str
    def complete(self, req: CompletionRequest) -> CompletionResult: ...
    def extract(self, req: ExtractionRequest[T]) -> ExtractionResult[T]: ...
```

- `extract` takes a Pydantic model and uses the vendor's native structured-output
  mechanism (Anthropic tool-use, OpenAI JSON schema, grammar-constrained decode for local
  models). Output is validated against the schema before it is allowed into the system.
- Implementations: `anthropic`, `openai`, `ollama` (local), `null` (deterministic stub for
  tests and for running the app with no key at all).
- Selected by config; **no business logic references a vendor**.
- Every call writes an `LLMRun` row: provider, model, purpose, prompt hash, token counts,
  latency, and the ID of whatever it produced. Prompts and completions are stored only if
  `LLM_STORE_PAYLOADS` is enabled (default off).
- Per-call **redaction and scoping**: only the specific interactions relevant to the task
  are sent. There is no "send the inbox to the model" path in the codebase, by
  construction — the enrichment API takes interaction IDs, never a query.

---

## 9. Security and privacy model

Threat model: a local single-user application holding a Gmail read token and the full text
of a personal negotiation. The realistic risks are token theft from disk, accidental
exfiltration to an LLM vendor, and leakage through logs.

- **Local only.** The server binds `127.0.0.1` by default. No auth is implemented, because
  adding a half-built login is worse than a loopback bind; binding to a non-loopback
  address requires setting an explicit config flag that also demands a token.
- **OAuth only.** No password field exists anywhere in the schema or UI.
- **Token at rest** is encrypted with a key from the OS keyring, falling back to a
  file-based key at `0600` with a startup warning. Tokens are never logged; a logging
  filter redacts `Bearer`, `refresh_token`, `access_token`, and `client_secret` patterns.
- **LLM egress is explicit**: a config flag, a per-provider allowlist, and a UI indicator
  showing what was sent. `null` provider means nothing leaves the machine; `ollama` means
  nothing leaves the machine either.
- **Raw mail stays local.** Blobs live under the data directory, never in the LLM path
  except as the specific normalized text of specific interactions.
- The SQLite file, blob store, and token file are all under one `data/` directory that is
  gitignored, so "delete the negotiation" is `rm -rf data/`.

---

## 10. Frontend

Vite + React + TypeScript, plain CSS with design tokens, one small fetch wrapper. No
component library, no state-management library, no CSS framework — the screens are dense
tables and detail panes, and a framework would add build weight without adding density.

Routes: `/` dashboard · `/dealers/:id` detail · `/compare` · `/profile` · `/review`
(low-confidence extractions awaiting human confirmation).

FastAPI serves the built assets in production mode, so the whole app is one process and
one URL. During development Vite proxies `/api` to the backend.

---

## 11. Deliberate non-goals (initially)

- No autonomous sending. Drafts only, with explicit EDIT / APPROVE / DISCARD. The
  `DraftMessage` state machine has a `SENT` state and a send port, but no send
  implementation is wired and the OAuth scope to do it is not requested.
- No multi-user, no hosted deployment, no auth.
- No live transcription. The upload endpoint and `transcript_source` schema exist so audio
  can be dropped in later without a migration.
- No vector search. The questions the user wants answered are structural, and SQL answers
  them exactly; embeddings would make them fuzzy.

---

## 12. Assumptions and open risks

These are the places where the design could be wrong. They are tracked, not buried.

| # | Assumption | Risk if wrong | Mitigation |
| --- | --- | --- | --- |
| A1 | `other_taxable_fees` is dealer-controlled; `other_non_tax_fees` is government pass-through | Normalized comparison misranks dealers | Both are editable per-offer with an explicit `is_dealer_controlled` flag on every fee line; the default is documented and shown in the UI |
| A2 | One active negotiation "campaign" at a time | A user shopping two cars at once sees merged state | Schema carries a nullable `campaign_id` from day one; UI exposes it later |
| A3 | A dealer is identified by email domain | Dealer groups sharing one domain across rooftops merge incorrectly | Domain is a *hint*; resolution also uses signature address/phone, and unresolved mail lands in a review queue rather than guessing |
| A4 | One vehicle per offer | Dealers sometimes quote alternates in one email | `Offer.vehicle_id` is required but a single interaction may produce multiple offers |
| A5 | Quoted-text stripping is reliable enough to trust | Extraction reads the buyer's own words as the dealer's commitment | Stripping is conservative; the stripped region is preserved and shown; automated-sender classification is a second guard |
| A6 | Timestamps are trustworthy for ordering | Contradiction detection picks the wrong "later" statement | Store both source timestamp and ingest timestamp; contradictions show both and never auto-resolve |
| A7 | The buyer's tax jurisdiction is fixed (PA / one ZIP) | Implied-tax-rate check false-positives | Rate lives in the buyer profile and is editable; a mismatch is a flag, never a correction |
| A8 | Dealer "OTD" always means the same thing | Silent comparison error | We never trust a quoted OTD alone — reconciliation against line items runs on every offer |

---

## 13. Repository layout

```
backend/
  app/
    main.py            FastAPI app, static mount, lifespan
    config.py          pydantic-settings
    db.py              engine, session, WAL pragmas
    models/            SQLAlchemy ORM
    schemas/           Pydantic request/response
    api/               routers
    services/          deterministic domain logic
    ingestion/         gmail/ transcript/ document/ manual/
    enrichment/        classify/ extract/ summarize/ draft (LLM consumers)
    llm/               provider abstraction + adapters
    fixtures/          the real-world Honda scenario as seed data
  alembic/
  tests/
frontend/
  src/                 routes, components, api client
data/                  gitignored: sqlite db, blobs, tokens
```
