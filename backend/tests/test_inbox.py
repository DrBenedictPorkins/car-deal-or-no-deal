"""The inbox: sweep, rank, claim.

The buyer decides what is a dealership by looking at a message and saying so.
These tests cover the two properties that make that cheap rather than tedious —
nothing is stored until it is claimed, and claiming one message claims its
neighbours — plus the idempotency that makes a mis-click harmless.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from conftest import make_dealer

from app.enums import InboxStatus
from app.ingestion.base import StaticSource
from app.ingestion.messages import RawMessage
from app.models import BuyerProfile, Campaign, Contact, Dealer, InboxMessage, Offer
from app.services import inbox as inbox_service

BASE = datetime(2026, 8, 30, 9, 0)

QUOTE = """Good morning,

Happy to put this in writing.

Selling price       $28,035.00
Dealer fee          $699.00
PA sales tax        $1,688.10
Out the door        $30,775.10

Chris Benton
Internet Sales"""


def message(
    key: str,
    *,
    sender: str = "chris.benton@hondaofwestport.example.test",
    subject: str = "Re: 2026 Civic Hatchback Sport",
    body: str = QUOTE,
    minutes: int = 0,
    thread: str = "<thread-wp@test>",
    to: str = "buyer@example.test",
) -> RawMessage:
    return RawMessage(
        source_system="demo",
        source_identifier=key,
        sent_at=BASE + timedelta(minutes=minutes),
        thread_identifier=thread,
        rfc822_message_id=f"<{key}@test>",
        from_address=sender,
        from_name="Chris Benton",
        to_addresses=(to,),
        subject=subject,
        body_text=body,
    )


@pytest.fixture()
def buyer(db):
    profile = BuyerProfile(
        id=1,
        email="buyer@example.test",
        display_name="Test Buyer",
        target_make="Honda",
        target_model="Civic Hatchback",
        expected_tax_rate_bp=600,
    )
    db.add(profile)
    db.flush()
    return profile


@pytest.fixture()
def campaign(db):
    row = Campaign(name="Civic", opened_at=BASE - timedelta(days=1), status="ACTIVE")
    db.add(row)
    db.flush()
    return row


@pytest.fixture()
def source():
    return StaticSource(
        [
            message("m1", minutes=0),
            message("m2", minutes=90, body="Following up — still available."),
            message(
                "m3",
                sender="edwin.sanchez@hondaofstamford.example.test",
                thread="<thread-st@test>",
                minutes=120,
                subject="Your Civic Sport Hatchback — numbers",
            ),
            message(
                "spam",
                sender="noreply@autoleadsnetwork.example.test",
                thread="<thread-blast@test>",
                minutes=200,
                subject="Your new car search",
                body="Still looking? Thousands of vehicles. Click here to unsubscribe.",
            ),
        ],
        name="demo",
    )


# ------------------------------------------------------------------- sweeping


def test_sweep_stores_metadata_but_not_bodies(db, buyer, campaign, source):
    """The privacy argument for letting the window be wide."""
    inbox_service.sweep(db, source, since=campaign.opened_at, campaign=campaign)

    rows = db.query(InboxMessage).all()
    assert len(rows) == 4
    row = next(r for r in rows if r.source_identifier == "m1")
    assert row.from_email == "chris.benton@hondaofwestport.example.test"
    assert row.subject
    assert row.snippet  # a preview, not the message
    assert "Out the door" in QUOTE
    assert not hasattr(row, "raw_content")
    # And nothing has entered the negotiation record yet.
    from app.models import Interaction

    assert db.query(Interaction).count() == 0


def test_sweeping_twice_adds_nothing(db, buyer, campaign, source):
    first = inbox_service.sweep(db, source, since=campaign.opened_at, campaign=campaign)
    second = inbox_service.sweep(db, source, since=campaign.opened_at, campaign=campaign)
    assert first.added == 4
    assert second.added == 0
    assert second.already_known == 4
    assert db.query(InboxMessage).count() == 4


def test_widening_the_window_is_safe(db, buyer, campaign, source):
    """Getting the start date wrong should cost nothing to correct."""
    narrow = inbox_service.sweep(
        db, source, since=BASE + timedelta(minutes=100), campaign=campaign
    )
    assert narrow.added == 2

    wide = inbox_service.sweep(db, source, since=BASE - timedelta(days=5), campaign=campaign)
    assert wide.added == 2
    assert wide.already_known == 2
    assert db.query(InboxMessage).count() == 4


def test_the_sweep_respects_the_start_date(db, buyer, campaign, source):
    report = inbox_service.sweep(
        db, source, since=BASE + timedelta(minutes=115), campaign=campaign
    )
    assert report.fetched == 2
    assert {r.source_identifier for r in db.query(InboxMessage).all()} == {"m3", "spam"}


# -------------------------------------------------------------------- ranking


def test_bulk_mail_sinks_and_dealership_mail_floats(db, buyer, campaign, source):
    inbox_service.sweep(db, source, since=campaign.opened_at, campaign=campaign)
    rows = inbox_service.listing(db)
    assert rows[-1].source_identifier == "spam"
    assert any("no-reply" in r for r in rows[-1].reasons)
    assert rows[0].score > rows[-1].score


def test_a_known_contact_lifts_a_message_to_the_top(db, buyer, campaign, source):
    dealer = make_dealer(db, "Honda of Westport", domains="hondaofwestport.example.test")
    db.add(
        Contact(
            dealer_id=dealer.id,
            name="Chris Benton",
            email="chris.benton@hondaofwestport.example.test",
        )
    )
    db.flush()
    inbox_service.sweep(db, source, since=campaign.opened_at, campaign=campaign)

    top = inbox_service.listing(db)[0]
    assert top.from_email == "chris.benton@hondaofwestport.example.test"
    assert "From a contact you already have" in top.reasons


def test_an_alias_is_the_strongest_signal(db, buyer, campaign):
    """The one identity marker the buyer controls, rather than the CRM."""
    make_dealer(db, "Honda of Westport", inquiry_alias="buyer+dl-westport@example.test")
    aliased = StaticSource(
        [message("via-crm", sender="rep@some-crm-vendor.test",
                 to="buyer+dl-westport@example.test")],
        name="demo",
    )
    inbox_service.sweep(db, aliased, since=campaign.opened_at, campaign=campaign)

    row = inbox_service.listing(db)[0]
    assert any("alias" in reason for reason in row.reasons)
    assert row.score >= 4


# ----------------------------------------------------- claiming a dealership


def test_claiming_a_message_creates_the_dealership_and_extracts_the_offer(
    db, buyer, campaign, source
):
    inbox_service.sweep(db, source, since=campaign.opened_at, campaign=campaign)
    row = db.query(InboxMessage).filter_by(source_identifier="m1").one()

    result = inbox_service.promote(db, row, source)

    assert result.created_dealer is True
    assert result.dealer.name == "Honda of Westport"
    assert result.learned_domain == "hondaofwestport.example.test"
    assert result.interaction is not None
    assert result.offer_id is not None

    offer = db.get(Offer, result.offer_id)
    assert offer.selling_price_cents == 2803500
    assert offer.quoted_otd_cents == 3077510

    db.refresh(row)
    assert row.status == InboxStatus.PROMOTED
    assert row.dealer_id == result.dealer.id


def test_claiming_one_message_claims_its_thread_and_sender(db, buyer, campaign, source):
    """Seven clicks for a negotiation, not seven a day."""
    inbox_service.sweep(db, source, since=campaign.opened_at, campaign=campaign)
    row = db.query(InboxMessage).filter_by(source_identifier="m1").one()

    result = inbox_service.promote(db, row, source)

    assert len(result.also_claimed) == 1
    claimed = db.query(InboxMessage).filter_by(source_identifier="m2").one()
    assert claimed.status == InboxStatus.PROMOTED
    assert claimed.dealer_id == result.dealer.id
    # The unrelated dealership and the blast are untouched.
    assert db.query(InboxMessage).filter_by(status=InboxStatus.NEW).count() == 2


def test_a_second_message_from_a_known_domain_folds_in(db, buyer, campaign, source):
    inbox_service.sweep(db, source, since=campaign.opened_at, campaign=campaign)
    first = db.query(InboxMessage).filter_by(source_identifier="m1").one()
    inbox_service.promote(db, first, source)

    # A different thread from the same dealership, arriving later.
    later = StaticSource(
        [message("m9", thread="<thread-new@test>", minutes=500,
                 body="One more thought on pricing.")],
        name="demo",
    )
    inbox_service.sweep(db, later, since=campaign.opened_at, campaign=campaign)
    row = db.query(InboxMessage).filter_by(source_identifier="m9").one()

    result = inbox_service.promote(db, row, later)
    assert result.created_dealer is False
    assert db.query(Dealer).count() == 1


def test_claiming_the_same_message_twice_changes_nothing(db, buyer, campaign, source):
    inbox_service.sweep(db, source, since=campaign.opened_at, campaign=campaign)
    row = db.query(InboxMessage).filter_by(source_identifier="m1").one()

    inbox_service.promote(db, row, source)
    before = (db.query(Dealer).count(), db.query(Offer).count())
    inbox_service.promote(db, row, source)

    assert (db.query(Dealer).count(), db.query(Offer).count()) == before


def test_folding_into_an_existing_dealership_learns_its_second_domain(
    db, buyer, campaign
):
    """A store's management domain is learned the first time it writes."""
    dealer = make_dealer(db, "Honda of Westport", domains="hondaofwestport.example.test")
    from_management = StaticSource(
        [message("mgmt", sender="gm@westport-auto-group.test", thread="<t-mgmt@test>")],
        name="demo",
    )
    inbox_service.sweep(db, from_management, since=campaign.opened_at, campaign=campaign)
    row = db.query(InboxMessage).filter_by(source_identifier="mgmt").one()

    result = inbox_service.promote(db, row, from_management, dealer_id=dealer.id)

    assert result.created_dealer is False
    assert result.learned_domain == "westport-auto-group.test"
    assert {d.domain for d in dealer.domains} == {
        "hondaofwestport.example.test",
        "westport-auto-group.test",
    }


def test_a_name_override_wins_over_the_guess(db, buyer, campaign, source):
    inbox_service.sweep(db, source, since=campaign.opened_at, campaign=campaign)
    row = db.query(InboxMessage).filter_by(source_identifier="m1").one()
    result = inbox_service.promote(db, row, source, dealer_name="Westport Honda (Post Road)")
    assert result.dealer.name == "Westport Honda (Post Road)"


def test_ignoring_hides_a_message_without_deleting_it(db, buyer, campaign, source):
    inbox_service.sweep(db, source, since=campaign.opened_at, campaign=campaign)
    row = db.query(InboxMessage).filter_by(source_identifier="spam").one()

    inbox_service.ignore(db, row)
    assert row.status == InboxStatus.IGNORED
    assert row.id not in {r.id for r in inbox_service.listing(db)}
    assert db.query(InboxMessage).filter_by(source_identifier="spam").one() is not None

    inbox_service.restore(db, row)
    assert row.id in {r.id for r in inbox_service.listing(db)}


# ------------------------------------------------------------- name guessing


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("hondaofwestport.example.test", "Honda of Westport"),
        ("hondaofstamford.example.test", "Honda of Stamford"),
        ("curryhonda.example.test", "Curry Honda"),
        ("oceanhondamilford.example.test", "Ocean Honda Milford"),
        ("westport-honda.test", "Westport Honda"),
    ],
)
def test_dealership_names_are_guessed_from_run_together_domains(domain, expected):
    """'ford' hides inside 'stamford'; anchored matching is what stops it winning."""
    row = InboxMessage(
        source_identifier="x", sent_at=BASE, from_email=f"someone@{domain}"
    )
    assert inbox_service.suggest_dealer_name(row) == expected


def test_an_organisation_named_in_the_body_beats_the_domain():
    row = InboxMessage(
        source_identifier="x",
        sent_at=BASE,
        from_email="rep@crm-vendor.test",
        snippet="Thank you for contacting Tarrytown Honda. To guarantee our best price…",
    )
    assert inbox_service.suggest_dealer_name(row) == "Tarrytown Honda"
