# dealbench

A local-first **dealership negotiation state engine**. Gmail, phone transcripts and
dealer documents go in; a structured, provenance-backed picture of every negotiation
comes out.

It is not an email client. The core is a state machine over dealers, offers and facts —
email is one input channel among several.

Open it and know, in under ten seconds:

1. Who have I contacted? 2. Who responded? 3. Who hasn't? 4. What's the best offer?
5. What changed? 6. Who owes the next response? 7. What should I do next?

- [ARCHITECTURE.md](ARCHITECTURE.md) — layers, pipeline, provenance, security, and the
  assumptions that could be wrong
- [DATA_MODEL.md](DATA_MODEL.md) — the schema and the arithmetic
- [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) — five phases with exit criteria
- [TESTING.md](TESTING.md) — the three transport modes, the golden dataset, sanitizing
  your real correspondence, and live Gmail
- [TODO.md](TODO.md) — running state, plus open questions

---

## Quick start

```bash
./scripts/setup.sh     # virtualenv, dependencies, database
./scripts/seed.sh      # optional: load the reference negotiation
./scripts/dev.sh       # http://127.0.0.1:5173  (API docs on :8756/docs)
```

Tests: `./scripts/test.sh` — 250 tests, offline and free. Live Gmail is opt-in; see
[TESTING.md](TESTING.md).

Nothing leaves your machine. No LLM is configured by default, no Gmail scope is
requested until you connect an account, and the database is a single SQLite file under
`data/` — which is gitignored, so `rm -rf data/` deletes the negotiation completely.

### CLI

```bash
cd backend && . .venv/bin/activate
python -m app.cli seed          # load the reference scenario (--live leaves it in progress)
python -m app.cli report        # print the dashboard to the terminal
python -m app.cli refresh       # re-run signals, states, contradictions, notifications
python -m app.cli reset --yes   # drop and recreate every table

python -m app.cli replay --golden --reset   # rebuild the negotiation message by message
python -m app.cli sanitize IN OUT           # real correspondence → safe fixtures
python -m app.cli gmail-auth                # OAuth; no password is ever requested
python -m app.cli gmail-sync                # incremental import (--historical for a full one)
```

`python -m app.cli report` on the seeded database:

```
  7 dealers · 7 responded · 0 silent · 2 with a priced offer
  Best OTD: $30,500.01 (Honda of Stamford)
  Best dealer-controlled: $27,954.00 (Curry Honda)
  You owe 5 · dealers owe 2 · 1 contradiction(s)

  DEALER                         OTD   DEALER COST  STATE           NEXT ACTION
  -----------------------------------------------------------------------------
  Honda of Stamford       $30,500.01    $28,426.42  Quote received  Get the dealer to reconcile…
  Honda of Westport       $30,775.10    $28,734.00  Quote received  Ask them to beat $30,500.01 OTD
  Curry Honda                      —    $27,954.00  Awaiting count  Follow up — dealer has owed…
  Ocean Honda Milford              —    $28,820.00  Awaiting count  Follow up — dealer has owed…
  Mount Kisco Honda                —             —  Responded       Restate that you need written…
  Tarrytown Honda                  —             —  Responded       Restate that you need written…
  White Plains Honda               —             —  Responded       Restate that you need written…
```

Curry has the cheapest *dealer-controlled* cost and no out-the-door number at all —
which is the entire reason both columns exist.

---

## What is built (Phase 1)

The engine, end to end, with no LLM and no Gmail — because if the engine is wrong, no
amount of ingestion will save it.

| | |
| --- | --- |
| **Pricing** | Dealer-controlled cost vs. OTD, reconciliation of quoted totals against line items, implied tax-rate check, clean-offer variant, version-to-version deltas |
| **State** | Declarative rules, append-only transition log, manual pinning that the engine records but does not override |
| **Derived** | Who owes a response, idle time, next recommended action — all recomputed, never stored |
| **Provenance** | Append-only facts with the source interaction and the verbatim quote; superseded values preserved forever |
| **Contradictions** | Cross-channel detection with both sides shown and no resolution proposed |
| **Behavior** | Transparency / responsiveness / price-competitiveness scores and friction points, each itemized — there is no opaque "AI score" in this product |
| **Comparison** | Four winners identified separately, because the cheapest OTD and the cleanest offer are often different dealers |
| **Drafts** | Generated from structured state, with EDIT / APPROVE / DISCARD. Sending is deliberately not implemented |
| **Queries** | The canned questions from the brief answered with exact SQL |

## What is built (Phase 2)

Ingestion, behind a transport abstraction that the negotiation engine cannot see past.

| | |
| --- | --- |
| **Sources** | Gmail (OAuth, historical import, `historyId` incremental sync with an expiry fallback), `.eml` directories, in-memory fixtures — all producing one `RawMessage` type |
| **Normalization** | HTML→text preserving table columns, quoted-reply and signature stripping with the stripped region kept |
| **Resolution** | known address → mail domain → Gmail thread → dealership named in the body → review queue. Never a guess |
| **Classification** | human vs. automated, rules first, with the reason recorded — including the lead-management pattern that replays the buyer's own inquiry |
| **Extraction** | deterministic parser reading labelled amounts into structured offers, with the verbatim quote and character offsets behind every figure |
| **Dedupe** | provider id, content fingerprint, and RFC-822 `Message-ID` — a re-import or a forwarded copy is one message |
| **Replay** | a corpus fed chronologically, with the whole board snapshotted after each event |
| **Sanitizer** | real correspondence → committable fixtures, deterministically |
| **Outbound** | draft → approve → send, behind three independent locks |

### Deliberately not built yet

LLM extraction (Phase 3), transcript and document pipelines (Phase 4), notifications at
scale and natural-language querying (Phase 5). The schema and the provider abstraction
for all of them are in place, so those phases add code rather than migrations.

Autonomous sending is still not a thing. There is now a send path, and it is locked
three ways: the transport must be enabled (off by default), safe mode must find the
recipient on an explicit allowlist, and a human must have approved that specific draft.
`PATCH /api/drafts/{id}` cannot set `SENT`, and `gmail.send` is only requested when
sending is switched on. See [TESTING.md](TESTING.md#5-outbound-safety).

---

## Design rules

1. **The LLM is never the database.** It classifies, extracts and drafts. Every fact it
   produces lands in a table with a source and a quote.
2. **The LLM never does arithmetic.** Extraction returns line items; Python computes
   totals, deltas and rankings. A model-supplied total is treated as a claim to verify.
3. **Money is integer cents**, end to end. No floats in the money path — enforced by a
   test over the schema.
4. **History is never overwritten.** Offers version, facts supersede, states append.
5. **Derived values are recomputed, not cached.** The dealer table stores no price, no
   idle time and no next action.
6. **Every score shows its reasons.**

---

## Layout

```
backend/app/
  models/       SQLAlchemy ORM          services/    deterministic domain logic
  schemas/      Pydantic request/response            ingestion/   channel adapters
  api/          FastAPI routers                      enrichment/  the only LLM consumers
  llm/          provider abstraction                 fixtures/    the reference scenario
frontend/src/   dashboard, dealer detail, comparison, profile
data/           gitignored: SQLite, blobs, tokens
```
