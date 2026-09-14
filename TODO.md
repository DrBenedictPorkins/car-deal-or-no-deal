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
- [x] Undisclosed dealer fees flagged rather than treated as zero
- [x] Verified end to end in a browser (dashboard, detail, comparison, profile)

## Phase 2 — Ingestion, replay and real-mail testing

- [x] Provider-neutral `RawMessage` / `MessageSource` / `MessageTransport` abstraction
- [x] Three transport modes: UNIT, REPLAY, LIVE GMAIL — with a test asserting the
      engine cannot tell them apart
- [x] OAuth 2.0 installed-app flow, narrowest-scope-per-configuration
- [x] Token encrypted at rest (keyring, else 0600 file with a warning)
- [x] Log redaction filter for token-shaped strings
- [x] Historical import, bounded, with a headers-only dry-run preview
- [x] Incremental sync via `historyId` with a date-window fallback on expiry
- [x] MIME normalization, HTML→text preserving table columns, quote/signature stripping
- [x] Identity resolution (address → domain → thread → named dealer → review queue)
- [x] Automated-vs-human classification with a recorded reason
- [x] Three-key dedupe: provider id, content fingerprint, RFC-822 `Message-ID`
- [x] Deterministic rule extractor with verbatim quotes and character offsets
- [x] Candidate → reconcile → commit, flagging rather than silently accepting
- [x] Replay engine with a per-event checkpoint of the whole board
- [x] Golden corpus (24 sanitized `.eml`) + hand-authored `manifest.json`
- [x] Fixture sanitizer, deterministic, with a residue report
- [x] Gmail fixture seeder (`gmail.insert`) for staging a test mailbox
- [x] Outbound send path behind three independent locks
- [x] Live Gmail test suite, opt-in and skipped by default
- [x] Review queue API (`/api/ingest/review`, assign by hand)
- [ ] Review queue **UI** — the API exists, the dashboard does not surface it yet
- [ ] Attachment download (`gmail.attachments`) — deferred to the Phase 4 document pipeline

## Phase 3 — LLM extraction and assistance

- [ ] Anthropic / OpenAI / Ollama adapters with native structured output
- [ ] Extraction schemas returning line items and quotes, never totals
- [ ] Held to the same golden manifest as the rule extractor
- [ ] Prose cases the rules miss: hedged conditions, split-clause prices, fuzzy deadlines
- [x] Candidate → reconcile → commit flow (built for the rule extractor; the model
      output goes through the same path)
- [x] Fact writing with quote offsets and superseding
- [x] Extraction-driven state transitions
- [ ] Thread and negotiation summaries
- [ ] LLM-narrated next action (rule still chooses)
- [x] Draft generation from full structured state (rule-based; the model rewrites the
      prose later, not the numbers)

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

### Real email corpus

Answered by building it — the defaults taken are listed so they can be overridden:

- The corpus is read as `.eml` (a directory, or a Gmail export converted to one). An
  `.mbox` splitter is a small addition if that is the shape you have.
- Real correspondence stays gitignored and local-only.
- The sanitizer produces a committable subset; the golden fixtures are sanitized
  reconstructions, and the real corpus never enters the repository.
- Dealer names are kept (business identities); people, addresses, phone numbers and
  VINs are replaced. `--keep-vins` and `keep_dealer_names=False` flip either way.
- Outbound messages are expected in the corpus and are needed for "who owes a response"
  and response-time scoring. A corpus with only inbound mail still works, but every
  dealer will read as owing you a reply.
- Golden expectations are checked in, hand-authored rather than generated.

Still open:

1. Is your corpus `.eml`, `.mbox`, or a Takeout archive? **(elaborate — only `.mbox`
   needs new code, and it is small)**
2. Do the real threads include PDF worksheets or screenshots? **(YES/NO — attachment
   metadata is already captured; downloading them is the Phase 4 document pipeline)**
3. Does your corpus include your own sent messages? **(YES/NO)**

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
13. Sending now exists behind three locks and is off by default. Should approved drafts
    also be written to Gmail as native drafts (`gmail.compose`), so you can send from
    your own mail client? **(YES/NO — currently: no, they stay in the app or go through
    the allowlisted send path)**
17. Should the dashboard surface the review queue, or is the API enough for now?
    **(YES/NO)**

### LLM

14. Which provider should be the default when you turn extraction on — Anthropic, OpenAI,
    or a local model via Ollama? **(elaborate)**
15. Is it acceptable for full email bodies of *dealer* correspondence to be sent to a
    hosted LLM, or must that stay on a local model? **(elaborate)**
16. Should extracted offers auto-commit when reconciliation is exact and confidence is
    high, or always wait for your review? **(elaborate; currently always review)**

---

## Notes from building Phase 2

- **Nothing about the corpus should be generated by the code under test.** The golden
  manifest is hand-authored from the original figures. An expectations file produced by
  the extractor would pass forever and mean nothing.
- **Time has to be injectable end to end.** Replaying a historical negotiation against
  wall clock makes every message look months overdue, so `now` is threaded through the
  state engine, contradictions, notifications and the dashboard. This also made the
  existing tests less fragile.
- **The label nearest the figure wins, not the first one read.** "got approval for $500
  off, so $28,820 selling price" parsed as a $500 car until claims were resolved by
  proximity rather than document order. Related: label matching must stop at the end of
  a line, or a columnar quote files every amount under the *next* row's label.
- **An accessory's name is the merge of its overlapping matches.** "VIN etching" fires
  `vin etch`, `etch` and `etching`; taking whichever reached furthest right named the
  line item "Etching".
- **A duplicate dealer on one domain is unresolvable, and that is correct.** A test
  failure turned out to be the test accidentally creating a second store on the same
  domain — the resolver refused to guess, which is exactly the designed behaviour.
- **Sanitizing has to be deterministic across the whole corpus, not per file.** One
  salesperson with a different fake address in each message becomes four strangers, and
  thread association quietly falls apart.
- **The allowlist belongs in a wrapper, not in each transport.** The realistic failure
  is a transport added later that forgets the check.

## Notes from building Phase 1

Things that turned out to matter and are now enforced:

- **An offer with no tax line has no OTD.** Summing a selling price and a doc fee and
  calling it "out the door" made Curry Honda — who never gave a tax figure — rank as the
  cheapest dealer on the dashboard. `computed_otd` is now `NULL` unless the government
  side is actually known.
- **Absence of a fee is not a fee of zero.** Ocean Honda quoted a selling price and
  nothing else; treating that as a $0 doc fee flattered them against dealers who
  disclosed theirs. Offers with no fee lines are flagged, and their dealer-controlled
  cost is presented as a floor.
- **Doc fees are taxable.** Stamford's quote only reconciles to exactly 6.00% PA tax with
  the doc fee in the tax base. That is now the default, and the implied-rate check exists
  precisely to catch the cases where it isn't.
- **An offer is proof the dealer engaged**, even when the message carrying it was never
  ingested — a quote read over the phone, a PDF dropped in. Both the state engine and the
  recommender check for an offer before concluding "never replied".
- **Convenience only means something among dealers who quoted.** Ranking a dealer who
  sent no price as "most convenient" is not a useful answer.
- A schema test caught a `Numeric` price column that had slipped into `campaign`. The
  structural tests earn their place.
