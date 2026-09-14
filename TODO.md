# TODO

Running implementation state. Updated as work progresses.

Legend: `[x]` done · `[~]` partial · `[ ]` not started

---

## Phase 0 — Design

- [x] ARCHITECTURE.md
- [x] DATA_MODEL.md
- [x] IMPLEMENTATION_PLAN.md
- [x] Assumptions and risks catalogued (ARCHITECTURE.md §12)

## Phase 1 — Core engine and manual workflow

- [x] Backend scaffolding (FastAPI, config, SQLite + WAL, Alembic)
- [x] Full ORM schema incl. tables reserved for Phases 2–5
- [x] Money as integer cents end to end
- [x] Buyer profile + campaign
- [x] Dealers / contacts / vehicles
- [x] Manual interactions (NOTE, EMAIL paste, CALL paste, SMS, IN_PERSON)
- [x] Offers with itemized lines, versioning, supersede chain
- [x] `pricing.py` — dealer-controlled cost, computed OTD, reconciliation, tax check,
      clean-offer variant, version deltas
- [x] `state_engine.py` — declarative rules, transition log, manual pin
- [x] `owes_response` / `idle_days` derivation
- [x] `recommender.py` — rule-based next action with reasons
- [x] `behavior.py` — signal-driven, fully explained scores
- [x] `comparison.py` — four separate winners, no composite score
- [x] `facts.py` — append-only, supersede, provenance lookup
- [x] `contradictions.py` — deterministic detectors
- [x] `notifications.py` — dedupe keys and cooldowns
- [x] LLM provider abstraction + `null` provider (no vendor code on a domain path)
- [x] Draft messages with EDIT / APPROVE / DISCARD (no send path)
- [x] React dashboard / dealer detail / comparison / profile
- [x] Honda reference fixture
- [x] Tests: pricing, versioning, state, contradictions, dedupe, layering
- [x] One-command startup

## Phase 2 — Gmail ingestion

- [ ] OAuth 2.0 installed-app flow (`gmail.readonly`), encrypted token at rest
- [ ] Log redaction filter for token-shaped strings
- [ ] Historical import with dry-run preview
- [ ] MIME normalization, HTML→text, quoted-reply and signature stripping
- [ ] Incremental sync via `historyId` with expiry fallback
- [ ] Identity resolution (exact → domain → signature → review queue)
- [ ] Automated-vs-human classification (rules first)
- [ ] Review queue UI

## Phase 3 — Structured extraction and assistance

- [ ] Anthropic / OpenAI / Ollama adapters with native structured output
- [ ] Extraction schemas returning line items and quotes, never totals
- [ ] Candidate → reconcile → commit flow with diff-before-commit
- [ ] Fact writing with quote offsets and superseding
- [ ] Extraction-driven state transitions
- [ ] Thread and negotiation summaries
- [ ] LLM-narrated next action (rule still chooses)
- [ ] Draft generation from full structured state

## Phase 4 — Transcripts, documents, contradictions

- [ ] `.txt` / `.md` / `.srt` / `.vtt` parsers → timecoded segments
- [ ] Audio upload endpoint + storage (transcription behind an interface)
- [ ] Dealer/contact suggestion for uploads
- [ ] PDF / image / text document pipeline, original always retained
- [ ] Cross-channel contradiction detection (call vs. written quote)
- [ ] Unified low-confidence review queue

## Phase 5 — Notifications, comparison depth, querying, reporting

- [ ] Notification cooldowns, digest mode, deadline watcher
- [ ] Financing-adjusted total cost and clawback/prepayment analysis
- [ ] Natural-language query → constrained query → real rows
- [ ] Negotiation report / export

---

## Open questions

Answering these unblocks the real-corpus integration testing and a few design choices.
YES/NO unless marked otherwise.

### Real email corpus (blocking for integration tests)

1. Will you provide the corpus as individual `.eml` files? **(YES/NO — if NO, is it a
   Gmail `.mbox` export, a Takeout archive, or should I read it live via the Gmail API?)**
2. Should the real corpus stay gitignored and local-only, never committed? **(YES/NO)**
3. Do you want a redaction pass that produces a sanitized, committable subset for CI
   (real names/addresses replaced, dollar amounts preserved)? **(YES/NO)**
4. Are the dealer names and salesperson names in your brief the real ones, i.e. may they
   appear in committed fixture code? **(YES/NO)**
5. Does the corpus include your own outbound messages, or only what dealers sent you?
   **(elaborate — outbound is needed for "who owes a response" and response-time scoring)**
6. Should I build the golden-file test as "corpus in → exact expected offers/states out",
   with the expected values checked in? **(YES/NO)**
7. Do any of the real threads include PDF worksheets or screenshots I should plan the
   document pipeline around now? **(YES/NO)**

### Product decisions

8. Is one campaign at a time sufficient, or do you expect to shop two vehicles
   simultaneously? **(elaborate)**
9. Assumption A1: I treat `other_taxable_fees` as dealer-controlled and
   `other_non_tax_fees` as government pass-through. Correct? **(YES/NO)**
10. For the OTD variance case (Westport quoted $30,775.10 but line items sum to
    $30,675.10), should the comparison rank on the **quoted** OTD? I currently do, and
    flag the $100. **(YES/NO)**
11. Should `FINALIST` be user-assigned only, or should the engine promote the top two
    dealers automatically? **(elaborate)**
12. What is your default follow-up threshold before a dealer counts as stalled — 24h, 48h,
    72h? **(elaborate; currently 48h to nudge, 7 days to `NO_RESPONSE`)**
13. Should drafts be pushed to Gmail as real Gmail drafts once Phase 2 lands (requires the
    `gmail.compose` scope), or stay inside this app until you explicitly say otherwise?
    **(YES/NO — currently: stay inside the app)**

### LLM

14. Which provider should be the default when you turn extraction on — Anthropic, OpenAI,
    or a local model via Ollama? **(elaborate)**
15. Is it acceptable for full email bodies of *dealer* correspondence to be sent to a
    hosted LLM, or must that stay on a local model? **(elaborate)**
16. Should extracted offers auto-commit when reconciliation is exact and confidence is
    high, or always wait for your review? **(elaborate; currently always review)**
