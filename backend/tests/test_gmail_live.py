"""End-to-end test against a real Gmail account.

Skipped unless explicitly enabled. It writes to a mailbox, so it refuses to run
anywhere but a dedicated test account:

    pip install -e '.[gmail]'
    export DEALBENCH_TEST_GMAIL=true
    export DEALBENCH_INGEST_MODE=GMAIL
    export DEALBENCH_GMAIL_CLIENT_SECRET_FILE=~/dealbench-oauth-client.json
    export DEALBENCH_GMAIL_ACCOUNT=dealbench.test.NNN@gmail.com
    export DEALBENCH_SEND_ALLOWLIST=dealbench.test.NNN@gmail.com
    export DEALBENCH_GMAIL_ALLOW_INSERT=true
    export DEALBENCH_GMAIL_IMPORT_QUERY='label:dealbench'
    python -m app.cli gmail-auth
    pytest tests/test_gmail_live.py -v

It covers the loop the brief asks for: stage the corpus in the mailbox, import it
historically, sync incrementally when something new arrives, check threading, dealer
and contact identification, extraction, and — only with sending switched on — a draft
approved and sent to the allowlisted address, with the reply coming back in.

Nothing here is mocked. That is the point: the replay suite already proves the engine,
and what remains unproven is Gmail itself.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

from app.config import Settings
from app.fixtures import golden_corpus
from app.ingestion.messages import RawMessage
from app.models import Contact, Interaction, Offer
from app.services import sync as sync_service

pytestmark = pytest.mark.live_gmail

ENABLED = os.getenv("DEALBENCH_TEST_GMAIL", "").lower() in {"1", "true", "yes"}
RUN_LABEL = f"dealbench-test-{uuid.uuid4().hex[:8]}"


def _settings() -> Settings:
    return Settings()


@pytest.fixture(scope="module")
def live():
    if not ENABLED:
        pytest.skip("live Gmail tests are off; set DEALBENCH_TEST_GMAIL=true")

    pytest.importorskip("googleapiclient", reason="pip install -e '.[gmail]'")

    settings = _settings()
    if settings.ingest_mode.upper() != "GMAIL":
        pytest.skip("set DEALBENCH_INGEST_MODE=GMAIL")
    if not settings.gmail_account:
        pytest.skip("set DEALBENCH_GMAIL_ACCOUNT to the dedicated test address")
    # The allowlist is what marks an account as disposable. Without this check a
    # mistyped env var would stage test mail in somebody's real inbox.
    allowlist = {a.lower() for a in settings.send_allowlist}
    assert settings.gmail_account.lower() in allowlist, (
        "the test account must be in DEALBENCH_SEND_ALLOWLIST — this is the guard "
        "against pointing the suite at a personal mailbox"
    )

    from app.ingestion.gmail import client as gmail_client
    from app.ingestion.gmail.auth import get_credentials
    from app.ingestion.gmail.source import GmailSource

    service = gmail_client.build_service(get_credentials(settings, interactive=False))
    source = GmailSource(settings, service=service)

    profile = source.profile()
    assert profile["emailAddress"].lower() in allowlist

    yield {"settings": settings, "service": service, "source": source}


@pytest.fixture(scope="module")
def staged(live):
    """Insert the golden corpus into the test mailbox, and clean it up afterwards."""
    from app.ingestion.gmail import seeder

    settings, service = live["settings"], live["service"]
    messages = golden_corpus.build()
    tagged = [
        _with_subject_tag(message, RUN_LABEL) for message in messages
    ]
    seeder.insert(service, tagged, settings=settings, account=settings.gmail_account)

    # Gmail indexes asynchronously; a search immediately after insert can come back
    # empty even though the messages are there.
    _wait_until(lambda: len(source_ids(live, RUN_LABEL)) >= len(tagged), timeout=60)

    yield tagged

    removed = seeder.purge(service, f'subject:"{RUN_LABEL}"')
    assert removed >= len(tagged)


def _with_subject_tag(message: RawMessage, tag: str) -> RawMessage:
    """Tag every subject so this run's mail can be found and deleted precisely."""
    from dataclasses import replace

    return replace(message, subject=f"[{tag}] {message.subject}")


def _wait_until(predicate, timeout: int = 60, interval: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


def source_ids(live, tag: str) -> list[str]:
    response = (
        live["service"]
        .users()
        .messages()
        .list(userId="me", q=f'subject:"{tag}"', maxResults=200)
        .execute()
    )
    return [item["id"] for item in response.get("messages", [])]


@pytest.fixture()
def prepared(db, live):
    golden_corpus.seed_profile(db)
    profile = db.get(type(golden_corpus.seed_profile(db)), 1)
    profile.email = live["settings"].gmail_account
    golden_corpus.seed_dealers(db)
    db.flush()
    return db


# --------------------------------------------------------------- the loop


def test_historical_import_pulls_the_staged_corpus(prepared, live, staged):
    settings = live["settings"]
    source = live["source"]
    report = sync_service.run(
        prepared, source, mode="historical", account=settings.gmail_account
    )
    assert report.fetched >= len(staged)
    assert report.created >= len(staged)
    assert report.cursor, "a historical import must leave a cursor for the next sync"


def test_a_second_sync_adds_nothing(prepared, live, staged):
    settings = live["settings"]
    sync_service.run(prepared, live["source"], mode="historical",
                     account=settings.gmail_account)
    before = prepared.query(Interaction).count()
    report = sync_service.run(prepared, live["source"], mode="incremental",
                              account=settings.gmail_account)
    assert report.created == 0
    assert prepared.query(Interaction).count() == before


def test_incremental_sync_picks_up_a_new_arrival(prepared, live, staged):
    from app.ingestion.gmail import seeder

    settings = live["settings"]
    sync_service.run(prepared, live["source"], mode="historical",
                     account=settings.gmail_account)
    before = prepared.query(Interaction).count()

    late = _with_subject_tag(
        RawMessage(
            source_system="golden",
            source_identifier="<late@dealbench.test>",
            sent_at=golden_corpus.BASE.replace(day=golden_corpus.BASE.day),
            rfc822_message_id="<late@dealbench.test>",
            from_address="chris.benton@hondaofwestport.example.test",
            from_name="Chris Benton",
            to_addresses=(settings.gmail_account,),
            subject="Re: revised numbers",
            body_text="Selling price $27,900.00\nSales tax $1,674.00\n"
            "Out the door $29,574.00",
        ),
        RUN_LABEL,
    )
    seeder.insert(live["service"], [late], settings=settings,
                  account=settings.gmail_account)
    _wait_until(lambda: len(source_ids(live, RUN_LABEL)) > before)

    report = sync_service.run(prepared, live["source"], mode="incremental",
                              account=settings.gmail_account)
    assert report.created >= 1
    assert prepared.query(Interaction).count() > before


def test_threads_dealers_and_contacts_are_identified_from_real_gmail(
    prepared, live, staged
):
    settings = live["settings"]
    sync_service.run(prepared, live["source"], mode="historical",
                     account=settings.gmail_account)

    # Gmail supplies its own thread ids; every message on one must land on one dealer.
    threads: dict[str, set[int | None]] = {}
    for interaction in prepared.query(Interaction).all():
        threads.setdefault(interaction.source_thread_identifier, set()).add(
            interaction.dealer_id
        )
    for thread, dealers in threads.items():
        assert len(dealers) == 1, f"thread {thread} split across {dealers}"

    names = {c.name for c in prepared.query(Contact).all()}
    assert {"Chris Benton", "Edwin Sanchez"} <= names

    corey = next((c for c in prepared.query(Contact).all() if c.name == "Corey Smith"), None)
    assert corey is not None and corey.actor_kind == "AUTOMATED"


def test_offers_extracted_from_real_gmail_match_the_golden_values(prepared, live, staged):
    settings = live["settings"]
    sync_service.run(prepared, live["source"], mode="historical",
                     account=settings.gmail_account)

    stamford = (
        prepared.query(Offer)
        .filter(Offer.dealer.has(name="Honda of Stamford"))
        .order_by(Offer.version)
        .all()
    )
    assert [o.quoted_otd_cents for o in stamford] == [3130622, 3050001]

    westport = (
        prepared.query(Offer).filter(Offer.dealer.has(name="Honda of Westport")).one()
    )
    assert westport.selling_price_cents == 2803500
    assert westport.quoted_otd_cents == 3077510


def test_a_draft_is_generated_from_the_imported_state(prepared, live, staged, client):
    settings = live["settings"]
    sync_service.run(prepared, live["source"], mode="historical",
                     account=settings.gmail_account)
    prepared.commit()

    dealer_id = (
        prepared.query(Offer).filter(Offer.dealer.has(name="Honda of Westport")).one()
    ).dealer_id
    response = client.post(f"/api/dealers/{dealer_id}/drafts/generate")
    assert response.status_code == 201
    assert response.json()["body"]


@pytest.mark.skipif(
    not Settings().send_enabled,
    reason="sending is off; set DEALBENCH_SEND_ENABLED=true to exercise the send path",
)
def test_an_approved_draft_reaches_the_test_account_and_comes_back(
    prepared, live, staged, client
):
    """The full outbound loop: approve, send to ourselves, sync, no duplicate."""
    from app.ingestion.base import get_transport
    from app.services import outbound

    settings = live["settings"]
    sync_service.run(prepared, live["source"], mode="historical",
                     account=settings.gmail_account)

    dealer = prepared.query(Contact).first().dealer
    contact = Contact(
        dealer_id=dealer.id,
        name="Loopback",
        email=settings.gmail_account,
        is_primary=False,
    )
    prepared.add(contact)
    prepared.flush()

    from app.models import DraftMessage

    draft = DraftMessage(
        dealer_id=dealer.id,
        contact_id=contact.id,
        subject=f"[{RUN_LABEL}] send-path check",
        body="This is an automated test message.",
        status="APPROVED",
    )
    prepared.add(draft)
    prepared.flush()

    outcome = outbound.send_draft(prepared, draft, transport=get_transport(settings))
    assert outcome.provider_message_id
    assert outcome.provider_thread_id

    # It is now in Sent. Syncing must recognise it as the message we already recorded.
    _wait_until(lambda: len(source_ids(live, RUN_LABEL)) > len(staged))
    before = prepared.query(Interaction).count()
    report = sync_service.run(prepared, live["source"], mode="historical",
                              account=settings.gmail_account)
    assert prepared.query(Interaction).count() == before, (
        "syncing the Sent copy created a duplicate interaction"
    )
    assert report.duplicates >= 1
