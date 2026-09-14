"""Pipeline behaviour: dedupe, resolution, and transport independence.

The brief's requirement that "business logic should behave identically regardless of
transport" is asserted here directly — the same messages are fed through the in-memory
source and through .eml files on disk, and the resulting database has to match.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from conftest import make_contact, make_dealer

from app.ingestion import eml
from app.ingestion.base import EmlDirectorySource, StaticSource
from app.ingestion.messages import RawMessage
from app.ingestion.pipeline import ingest, ingest_many
from app.models import BuyerProfile, Contact, EmailSource, Interaction, Offer
from app.services import sync as sync_service

BASE = datetime(2026, 8, 30, 9, 0)


@pytest.fixture()
def buyer(db) -> BuyerProfile:
    profile = BuyerProfile(id=1, email="buyer@example.test", display_name="Test Buyer",
                           expected_tax_rate_bp=600)
    db.add(profile)
    db.flush()
    return profile


def message(
    key: str = "m1",
    *,
    sender: str = "chris@westport.example.test",
    to: str = "buyer@example.test",
    body: str = "Selling price $28,035.00\nSales tax $1,688.10\nOut the door $30,775.10",
    minutes: int = 0,
    thread: str = "<thread-1@test>",
    source_system: str = "gmail",
    rfc822: str | None = None,
    subject: str = "Re: pricing",
) -> RawMessage:
    return RawMessage(
        source_system=source_system,
        source_identifier=key,
        sent_at=BASE + timedelta(minutes=minutes),
        thread_identifier=thread,
        rfc822_message_id=rfc822 or f"<{key}@westport.example.test>",
        from_address=sender,
        from_name="Chris Benton",
        to_addresses=(to,),
        subject=subject,
        body_text=body,
    )


@pytest.fixture()
def westport(db):
    return make_dealer(db, "Honda of Westport", email_domains="westport.example.test")


# ------------------------------------------------------------------- dedupe


def test_the_same_message_twice_creates_one_interaction(db, buyer, westport):
    first = ingest(db, message())
    second = ingest(db, message())
    assert first.created is True
    assert second.duplicate is True
    assert second.duplicate_of == first.interaction.id
    assert db.query(Interaction).count() == 1


def test_a_reimport_under_a_new_provider_id_is_still_one_message(db, buyer, westport):
    """Forwarding and re-imports change Gmail's id but not the RFC-822 Message-ID."""
    ingest(db, message("gmail-1", rfc822="<original@westport.example.test>"))
    again = ingest(db, message("gmail-2", rfc822="<original@westport.example.test>"))
    assert again.duplicate is True
    assert db.query(Interaction).count() == 1


def test_identical_boilerplate_from_two_dealers_is_two_messages(db, buyer, westport):
    make_dealer(db, "Curry Honda", email_domains="curry.example.test")
    ingest(db, message("a", sender="chris@westport.example.test", thread="<t-a@test>"))
    ingest(db, message("b", sender="jess@curry.example.test", thread="<t-b@test>"))
    assert db.query(Interaction).count() == 2


def test_a_full_resync_adds_nothing_the_second_time(db, buyer, westport):
    messages = [message(f"m{i}", minutes=i * 10, thread=f"<t{i}@test>") for i in range(4)]
    source = StaticSource(messages, name="gmail")

    first = sync_service.run(db, source, mode="historical")
    second = sync_service.run(db, source, mode="historical")

    assert first.created == 4
    assert second.created == 0
    assert second.duplicates == 4
    assert db.query(Interaction).count() == 4


def test_an_incremental_sync_picks_up_only_what_is_new(db, buyer, westport):
    early = [message(f"m{i}", minutes=i, thread=f"<t{i}@test>") for i in range(3)]
    source = StaticSource(early, name="gmail")
    sync_service.run(db, source, mode="historical")

    later = StaticSource(
        [*early, message("m9", minutes=90, thread="<t9@test>")], name="gmail"
    )
    report = sync_service.run(db, later, mode="incremental")

    assert report.created == 1
    assert db.query(Interaction).count() == 4


# ---------------------------------------------------------------- resolution


def test_a_known_contact_address_resolves_exactly(db, buyer, westport):
    contact = make_contact(db, westport, "Chris Benton", email="chris@westport.example.test")
    result = ingest(db, message())
    assert result.dealer_id == westport.id
    assert result.contact_id == contact.id


def test_a_new_sender_at_a_known_domain_becomes_a_contact(db, buyer, westport):
    result = ingest(db, message(sender="newperson@westport.example.test"))
    assert result.dealer_id == westport.id
    contact = db.get(Contact, result.contact_id)
    assert contact.email == "newperson@westport.example.test"


def test_a_shared_domain_goes_to_review_rather_than_guessing(db, buyer):
    """Dealer groups share domains; a wrong merge corrupts the whole comparison."""
    make_dealer(db, "Group Store A", email_domains="autogroup.example.test")
    make_dealer(db, "Group Store B", email_domains="autogroup.example.test")
    result = ingest(db, message(sender="sales@autogroup.example.test"))
    assert result.dealer_id is None
    assert result.needs_review is True
    assert "2 dealers" in " ".join(result.reasons)


def test_a_later_message_joins_its_thread_even_from_a_new_domain(db, buyer, westport):
    ingest(db, message("m1"))
    result = ingest(
        db, message("m2", sender="chris@someothermail.example.test", minutes=30)
    )
    assert result.dealer_id == westport.id


def test_the_buyers_own_message_is_outbound(db, buyer, westport):
    result = ingest(
        db,
        message(
            "out-1", sender="buyer@example.test", to="chris@westport.example.test",
            body="Could you send an itemized OTD?",
        ),
    )
    assert result.direction == "OUTBOUND"
    assert result.dealer_id == westport.id
    assert result.offer is None  # nothing is extracted from the buyer's own words


def test_email_metadata_is_preserved(db, buyer, westport):
    result = ingest(db, message())
    source = db.get(EmailSource, result.interaction.id)
    assert source.provider_message_id == "m1"
    assert source.provider_thread_id == "<thread-1@test>"
    assert source.rfc822_message_id == "<m1@westport.example.test>"
    assert source.from_email == "chris@westport.example.test"


# ------------------------------------------------------- transport equivalence


def _fingerprint(db) -> list[tuple]:
    """What the negotiation looks like, independent of how the mail arrived."""
    return sorted(
        (
            interaction.dealer.name if interaction.dealer else None,
            interaction.direction,
            interaction.actor_kind,
            interaction.subject,
            interaction.normalized_content,
        )
        for interaction in db.query(Interaction).all()
    ) + sorted(
        (offer.dealer.name, offer.version, offer.selling_price_cents, offer.quoted_otd_cents)
        for offer in db.query(Offer).all()
    )


def test_the_same_messages_produce_the_same_state_through_any_transport(
    db, buyer, westport, tmp_path
):
    messages = [
        message("m1", minutes=0, thread="<t1@test>"),
        message(
            "m2",
            minutes=60,
            thread="<t1@test>",
            body="Revised: Selling price $27,500.00\nSales tax $1,650.00\n"
            "Out the door $29,150.00",
        ),
    ]

    ingest_many(db, messages)
    via_memory = _fingerprint(db)

    # Wipe what ingestion produced — but not the dealership itself, since a second
    # store on the same domain is exactly what the resolver refuses to disambiguate.
    for model in (Offer, EmailSource, Interaction, Contact):
        db.query(model).delete()
    db.flush()

    for index, raw in enumerate(messages):
        (tmp_path / f"{index}.eml").write_bytes(eml.to_bytes(raw))
    from_disk = EmlDirectorySource(tmp_path, name="gmail").fetch_all()
    ingest_many(db, from_disk)
    via_disk = _fingerprint(db)

    assert via_memory == via_disk


def test_an_eml_directory_supports_incremental_sync_too(tmp_path):
    """Replay mode exercises the same cursor path Gmail does."""
    for index in range(2):
        (tmp_path / f"{index}.eml").write_bytes(
            eml.to_bytes(message(f"m{index}", minutes=index * 60))
        )
    source = EmlDirectorySource(tmp_path)

    first = source.fetch_incremental(None)
    assert len(first.messages) == 2

    (tmp_path / "2.eml").write_bytes(eml.to_bytes(message("m2", minutes=300)))
    second = source.fetch_incremental(first.cursor)
    assert [m.source_identifier for m in second.messages] == ["<m2@westport.example.test>"]
