# Implementation Plan

Phasing follows the brief. Each phase ends with something usable, not a half-wired layer.
Phase 1 is implemented in this branch; later phases list their exit criteria so scope
doesn't drift.

Running task state lives in [TODO.md](TODO.md).

---

## Phase 1 — Core engine and manual workflow ✅ (this branch)

**Goal:** the negotiation state engine works end to end with no LLM and no Gmail. If the
engine is wrong, no amount of ingestion will save it, so it gets built and tested first.

Delivered:

- Project scaffolding: FastAPI app, config, SQLite with WAL, Alembic baseline, test
  harness, single-command startup.
- Full schema from [DATA_MODEL.md](DATA_MODEL.md) — including tables Phases 2–5 will fill
  (`email_source`, `transcript_source`, `document`, `llm_run`, `sync_state`), so later
  phases add code, not migrations.
- Buyer profile + campaign CRUD.
- Dealers, contacts, vehicles CRUD.
- Manual interactions: NOTE / EMAIL (paste) / CALL (paste) / SMS / IN_PERSON.
- Offers with itemized lines, versioning, supersede chain.
- `pricing.py`: dealer-controlled cost, computed OTD, OTD reconciliation, implied tax
  rate, clean-offer variant, discount from MSRP, version-to-version deltas.
- `state_engine.py`: declarative rules, transition log, manual pin, `owes_response`,
  `idle_days`.
- `recommender.py`: rule-based next action with an explicit reason, referencing the best
  competing offer and the buyer profile's local-dealer premium.
- `behavior.py`: signal-based, fully explained scores.
- `comparison.py`: side-by-side with cheapest-OTD / cheapest-dealer-cost / most-convenient
  / cleanest-offer winners identified separately.
- `facts.py`: append-only writes, supersede chain, provenance retrieval.
- `contradictions.py`: deterministic detectors (add-on presence, numeric conflict,
  demo/loaner status, financing conditions, broken commitments).
- Notifications with dedupe.
- LLM provider abstraction with a `null` provider; no vendor code on a domain path.
- React dashboard, dealer detail (timeline + facts + offers + contradictions), comparison,
  profile editor.
- Real-world Honda fixture (Stamford, Westport, Curry, Ocean, White Plains, Mount Kisco,
  Tarrytown) loadable with one command.
- Tests over pricing, normalization, versioning, state transitions, contradictions,
  dedupe, and the layering invariant.

**Exit criteria (met):** loading the fixture reproduces the real negotiation — Stamford
final beats Westport by $275.09 OTD and $307.58 on dealer-controlled cost, the Westport
$100 unexplained charge is flagged, and the dashboard answers all seven UX questions.

---

## Phase 2 — Gmail ingestion

**Goal:** mail arrives by itself and lands on the right dealer.

1. OAuth 2.0 installed-app flow, loopback redirect, `gmail.readonly` only. Token
   encrypted via OS keyring with a `0600` file fallback; redaction filter on the logger.
2. Historical import: query by sender domain / label / date window, with a dry-run preview
   before anything is written.
3. MIME normalization: multipart walk, HTML→text, charset handling, quoted-reply and
   signature stripping with the stripped region preserved.
4. Incremental sync via `users.history.list` + stored `historyId`, with a bounded
   date-window fallback when the cursor expires.
5. Identity resolution: exact email match → domain match → signature parse → review queue.
   Never guess silently.
6. Automated-vs-human classification, rule-first (`no-reply`, lead-management domains,
   verbatim restatement of the buyer's own inquiry, send cadence), LLM only for residue.
7. Review queue UI for unresolved senders and ambiguous threads.

**Exit criteria:** a full historical import followed by two incremental syncs produces
zero duplicate interactions; every message is attached to a dealer or explicitly parked in
review; the automated lead-management pattern from the source workflow is detected.

**Risks:** `historyId` expiry; forwarded duplicates; dealer groups sharing a domain (A3).

---

## Phase 3 — Structured extraction and assistance

**Goal:** offers and facts appear without typing them.

1. Provider adapters: Anthropic, OpenAI, Ollama — each using native structured output.
   Schema validation before persistence; a schema failure is a retry, then a review flag.
2. Extraction schemas: `OfferExtraction`, `VehicleFacts`, `QuestionsAndCommitments`,
   `FinancingConditions`. Each returns **line items and quotes, never totals**.
3. Candidate → reconcile → commit: extracted offers land as `needs_review` when confidence
   is low or reconciliation fails; the UI diffs candidate against current before commit.
4. Fact writing with quote offsets, superseding prior facts.
5. Automatic state transitions driven by extraction results.
6. Thread summaries and a negotiation summary per dealer.
7. LLM-narrated next action — the *rule* still chooses the move; the model explains it.
8. Draft generation using the full structured state (best competitor, buyer profile,
   open questions), with EDIT / APPROVE / DISCARD. No sending.

**Exit criteria:** replaying the fixture emails through extraction reproduces the
hand-entered Phase 1 offers within exact cents on every line item; every extracted fact is
clickable back to its quote; no arithmetic originates from the model.

**Risks:** extraction of financing conditionals ("$27,779 *if* you finance"); hallucinated
add-ons; the model summarizing the buyer's quoted text as the dealer's position (A5).

---

## Phase 4 — Transcripts, documents, contradictions

1. Transcript parsers: `.txt`, `.md`, `.srt`, `.vtt` → `transcript_segment` with
   timecodes; speaker-party attribution with manual correction.
2. Audio upload endpoint + storage, transcription behind a provider interface (not
   implemented, not stubbed into the UI as if it works).
3. Dealer/contact suggestion for an uploaded transcript, with confirmation.
4. Document pipeline: PDF text extraction, image OCR, worksheet → `Offer` candidate; the
   original file is always retained and viewable.
5. Cross-channel contradiction detection with the call/email example as the canonical
   test: "no mandatory add-ons" at 2:14 PM vs. $398 of add-ons at 4:03 PM.
6. Low-confidence review queue unified across email, transcript, and document.

**Exit criteria:** the canonical contradiction is detected from a `.vtt` plus an email,
both sides are cited with timestamps, and the system proposes no resolution.

---

## Phase 5 — Notifications, comparison depth, querying, reporting

1. Notification rules with cooldowns and a digest mode; deadline watcher.
2. Comparison enhancements: financing-adjusted total cost, distance/time cost, clean-offer
   ranking.
3. Financing analysis: clawback / prepayment / minimum-term detection, with an explicit
   refusal to recommend finance-then-payoff until all conditions are confirmed.
4. Natural-language query over the structured model — the model writes a *constrained
   query*, application code executes it and renders real rows. The model never invents an
   answer from memory.
5. Negotiation report: timeline, price progression, savings achieved, dealer behavior
   summary — exportable.

---

## Cross-cutting: testing strategy

The brief's test list maps to these suites:

| Area | Tests |
| --- | --- |
| Pricing | normalization, OTD reconciliation, tax-rate inference, clean-offer, deltas, rounding at cent boundaries |
| Offers | versioning, supersede chain, single-current invariant, add-on totals from lines |
| State | each rule, pinning, transition log, idle computation, owes-response |
| Ingestion | duplicate Gmail ingestion (same id, same RFC-822 id, re-import), thread association, dealer association, multiple contacts per dealer |
| Classification | automated vs. human, verbatim-restatement detection |
| Extraction | financing-conditioned prices, missing fields, malformed transcripts, corrections superseding earlier facts |
| Contradictions | cross-channel, numeric, presence, broken commitment |
| Structure | layering invariant (no LLM import in `services/`), no float in the money path |

Determinism rule: arithmetic and invariants are covered by tests that do not call a model.
LLM-dependent behavior is tested against recorded fixtures with the `null` provider, so
the suite runs offline and free.

---

## Integration testing with real dealership email

The user has a real corpus. Bringing it in needs decisions that shouldn't be guessed —
see the questions in [TODO.md](TODO.md#open-questions). The intended shape:

- A `tests/fixtures/corpus/` directory of `.eml` files (or a Gmail export), gitignored by
  default so real correspondence is never committed.
- A redaction pass producing a sanitized, committable subset for CI.
- Golden-file tests: corpus in → expected offers, states, and contradictions out, asserted
  on exact cents and exact state codes.
- The same corpus drives Phase 2 dedupe tests (import twice, expect no change) and Phase 3
  extraction accuracy scoring.

---

## Startup

```bash
./scripts/dev.sh            # backend + frontend, hot reload
./scripts/seed.sh           # load the Honda reference scenario
pytest                      # backend tests
```

Details in [README.md](README.md).
