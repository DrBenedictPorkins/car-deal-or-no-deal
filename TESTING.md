# Testing

Three transport modes, one engine. The negotiation logic cannot tell them apart, which
is the property that makes any of this worth doing: a replay test is evidence about
production, not about the harness.

| Mode | Source | Network | Used for |
| --- | --- | --- | --- |
| **UNIT** | messages constructed in the test | none | pricing, state, extraction, dedupe |
| **DEMO** | the reference negotiation, dated to the last few days | none | clicking through the inbox and the dashboard by hand |
| **REPLAY** | `.eml` files on disk, fed chronologically | none | the whole negotiation, end to end |
| **LIVE GMAIL** | the real Gmail API, dedicated account | yes | Gmail itself — OAuth, threading, sync, send |

```bash
./scripts/test.sh                      # UNIT + REPLAY. Offline, free, ~13s.
pytest -m live_gmail                   # skipped unless explicitly configured
```

The default run is offline by construction: `addopts = -m 'not live_gmail'`, and the
Google client libraries are an optional dependency the core never imports.

---

## 1. Replay mode

Feeds a corpus into the ingestion pipeline one message at a time, in the order it was
actually sent, and snapshots the whole board after each one.

```bash
python -m app.cli replay --golden --reset
```

```
 T11  Aug 31 11:24  ← Honda of Westport   Re: 2026 Civic Hatchback Sport    best=Honda of Westport 30,775.10
 T12  Aug 31 13:02  ← Curry Honda         Re: 2026 Civic Hatchback Sport    best=Honda of Westport 30,775.10
 T13  Aug 31 16:03  ← Honda of Stamford   Your Civic Sport Hatchback        best=Honda of Westport 30,775.10
 …
 T20  Sep 02 15:40  ← Honda of Stamford   Re: … revised                     best=Honda of Stamford 30,500.01
```

Each event is evaluated **at its own timestamp**, so idle times, follow-up thresholds
and deadlines behave as they did on the day rather than relative to whenever the suite
runs. Without that, every historical message reads as months overdue.

A checkpoint carries the state of every dealer, so assertions read as statements about
the negotiation:

```python
point = checkpoint_for(run, "t7-stamford-quote-1")

assert point.best_otd_dealer == "Honda of Westport"     # $31,306.22 doesn't displace it
assert point.state_of("Honda of Stamford") == "QUOTE_RECEIVED"
assert point["Tarrytown Honda"].has_friction("INSISTS_ON_VISIT")
assert point["Curry Honda"].has_friction("INSISTS_ON_PHONE_CALL")
```

Also exposed over HTTP:

```bash
curl -XPOST localhost:8756/api/ingest/replay -d '{"use_golden": true}'
```

---

## 2. The golden dataset

`backend/tests/fixtures/golden/` holds the reference negotiation as 24 sanitized `.eml`
files plus `manifest.json`.

**The manifest is hand-authored** from the original figures, not generated from the
extractor. If the expectations came out of the code under test they would only prove
the code agrees with itself.

```json
"t5-westport-quote": {
  "dealer": "Honda of Westport",
  "actor_kind": "HUMAN",
  "offer": {
    "selling_price_cents": 2803500,
    "doc_fee_cents": 69900,
    "registration_cents": 25300,
    "tax_cents": 168810,
    "quoted_otd_cents": 3077510,
    "financing_required": false,
    "add_ons": []
  },
  "derived": {
    "dealer_controlled_cents": 2873400,
    "computed_otd_cents": 3067510,
    "otd_variance_cents": 10000,
    "otd_reconciled": false
  }
}
```

That last block is the one worth reading twice: Westport's quoted out-the-door figure is
**$100 more than its own line items add up to**. The fixture preserves the discrepancy
and the test asserts it is flagged, because reconciling it away silently is exactly the
behaviour this application exists to prevent.

The corpus is regenerated from `app/fixtures/golden_corpus.py`:

```bash
python -m app.cli golden-build
```

`.eml` rather than Python objects on purpose — the tests then exercise header parsing,
threading and MIME on the way in, which is where ingestion bugs live.

### What it asserts

| | |
| --- | --- |
| Ingestion | every message once; re-running the corpus changes nothing |
| Threading | every message in a Gmail thread lands on one dealer |
| Identification | dealerships seeded, **contacts discovered** — two people at one store stay apart |
| Classification | the lead-system follow-up is caught by its restatement of the buyer's own inquiry; the genuine reply from the same dealership is not tarred with it |
| Extraction | every figure in the manifest, to the cent, plus the quote it came from |
| Timeline | best offer, states and friction at each checkpoint |
| Outcomes | Stamford improves by **$806.21**, beats Westport by **$275.09** OTD and **$307.58** on dealer-controlled cost, **$1,760.58** off MSRP |
| Noise | a broadcast belonging to no dealership lands in the review queue rather than being attached to the nearest dealer |

---

## 3. Your real correspondence

Real mail never enters the repository. `backend/tests/fixtures/corpus/` is gitignored,
and so is `mapping.private.json`.

```bash
python -m app.cli sanitize ~/civic-emails backend/tests/fixtures/corpus \
    --buyer-email you@example.com \
    --buyer-name "Your Name"
```

Preserved: prices, fees, add-on names, wording, timestamps, ordering, thread
relationships. Replaced: names, addresses, phone numbers, street addresses, VINs.

Replacement is **deterministic** — the same real value always becomes the same fake one,
across every file. That is what keeps threading intact; if one salesperson's address
varied per message, dealer resolution would see four strangers.

Phone numbers land in `555-01xx`, which is reserved for fiction and cannot be dialled.
Attachments are dropped rather than copied, because a PDF worksheet cannot be
pattern-scrubbed.

The tool reports what it replaced and flags anything that still looks personal:

```
Wrote 31 file(s) to backend/tests/fixtures/corpus
Replaced: 12 email, 4 domain, 9 name, 6 phone, 2 address, 1 vin
  Review these before committing:
    - <m17@…>: phone not rewritten: 1-800-HONDA-4U
```

Residue is **reported, not removed**. A surprise there means the corpus contains a shape
the patterns do not cover, and that is a human's call. Read a few files either way.

Then replay it:

```bash
python -m app.cli replay --directory backend/tests/fixtures/corpus --reset
```

---

## 4. Live Gmail

Uses a dedicated test account and real Gmail API calls. It writes to a mailbox, so it
refuses to run against anything it has not been told is disposable.

### Setup

1. Create a throwaway Gmail account.
2. Google Cloud console → new project → enable the Gmail API → OAuth client ID →
   **Desktop app** → download the JSON.
3. Configure and authorize:

```bash
pip install -e '.[gmail]'

export DEALBENCH_INGEST_MODE=GMAIL
export DEALBENCH_GMAIL_CLIENT_SECRET_FILE=~/dealbench-oauth-client.json
export DEALBENCH_GMAIL_ACCOUNT=dealbench.test.001@gmail.com
export DEALBENCH_SEND_ALLOWLIST=dealbench.test.001@gmail.com
export DEALBENCH_GMAIL_ALLOW_INSERT=true
export DEALBENCH_GMAIL_IMPORT_QUERY='subject:dealbench-test'

python -m app.cli gmail-auth      # opens a browser; no password is ever typed into this app
python -m app.cli gmail-seed      # stages the golden corpus in the mailbox
python -m app.cli gmail-sync --historical

export DEALBENCH_TEST_GMAIL=true
pytest -m live_gmail -v
```

Clean up with `python -m app.cli gmail-seed --purge 'subject:dealbench-test'`.

### What it covers

Import the corpus → historical sync → incremental sync when something new arrives →
Gmail thread association → dealer and contact identification → offer extraction →
draft generation → (optionally) send to the allowlisted address → the sent copy syncing
back **without creating a duplicate interaction**.

### Scopes

Requested at the narrowest level the configuration needs, and never implicitly:

| Setting | Scope added |
| --- | --- |
| *(default)* | `gmail.readonly` |
| `SEND_ENABLED=true` | `gmail.send` |
| `GMAIL_ALLOW_INSERT=true` | `gmail.insert` |

`gmail.insert` writes a message into a mailbox without sending it anywhere — no
dealership receives test mail.

### Guards

`gmail-seed` refuses to run unless **all** of these hold:

1. `DEALBENCH_GMAIL_ALLOW_INSERT=true`
2. `DEALBENCH_SEND_ALLOWLIST` is non-empty
3. the target account is *in* that allowlist

The live test suite additionally asserts that the authorized account's own address is
allowlisted before it stages anything. A mistyped environment variable fails; it does
not fill somebody's real inbox with test mail.

---

## 5. Outbound safety

Three independent locks, all of which must be open before a byte leaves:

1. **Transport enabled** — `DEALBENCH_SEND_ENABLED`, default `false`. The default
   transport refuses and explains itself.
2. **Safe mode** — `DEALBENCH_SEND_SAFE_MODE`, default `true`. Every recipient, To and
   Cc alike, must be in `DEALBENCH_SEND_ALLOWLIST`. One unlisted address blocks the
   whole message; a partial send is worse than none.
3. **Human approval** — only a draft in `APPROVED` may be sent, and only once.

The allowlist check lives in `SafeTransport`, which **wraps** whatever transport is
configured rather than being reimplemented per transport. The failure this guards
against is a future transport that forgets, and forgetting means real mail to a real
dealership from a test run.

`PATCH /api/drafts/{id}` cannot set `SENT`. Sending goes through
`POST /api/ingest/drafts/{id}/send`, which enforces all three locks and records the
provider's message and thread ids — so the sent copy deduplicates on the next sync and
the dealer's reply threads onto it.

---

## 6. Test inventory

```
test_pricing.py            normalization, reconciliation, tax inference, deltas
test_offers.py             versioning, supersede chain, single-current invariant
test_state_engine.py       every rule, pinning, transition log
test_context.py            who owes a response, idle time
test_facts.py              append-only provenance, supersede, no-op re-record
test_contradictions.py     cross-channel, numeric, presence, broken promise
test_recommender.py        next action, local premium, benchmark selection
test_scenario.py           the Phase 1 hand-entered fixture
test_normalize.py          HTML flattening, quote and signature stripping
test_classify.py           human vs automated, restatement detection
test_extract_rules.py      label-to-figure matching, financing, add-ons
test_ingestion_pipeline.py dedupe, resolution, transport equivalence
test_golden_replay.py      the full negotiation against the manifest
test_sanitize.py           what survives, what does not, determinism
test_gmail_client.py       Gmail payload mapping, no network
test_outbound_safety.py    the three locks
test_gmail_live.py         real Gmail (opt-in)
test_api.py                endpoint surface
test_layering.py           no LLM in services, no float in the money path
```

Determinism rule: arithmetic and invariants are covered by tests that call no model and
open no socket. LLM behaviour, when Phase 3 lands, is tested against recorded fixtures
with the `null` provider — and the golden manifest is what the model extractor will be
held to, exactly as the rule extractor is now.
