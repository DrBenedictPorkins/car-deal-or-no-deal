"""Multi-domain dealerships, and the ambiguity that comes with them.

A dealership routinely has more than one mail domain — the store's own, one for
management, and whatever its CRM sends from. And the reverse is also true: a
dealer group shares one domain across several rooftops. Both shapes have to work,
and the second one must never resolve by guessing.
"""

from __future__ import annotations

from datetime import datetime

from conftest import make_contact, make_dealer

from app.enums import DomainKind
from app.ingestion.messages import RawMessage
from app.ingestion.pipeline import ingest
from app.ingestion.resolve import learn_domain
from app.models import BuyerProfile, DealerDomain

BASE = datetime(2026, 8, 30, 9, 0)


def message(sender: str, *, key: str = "m1", thread: str = "<t1@test>",
            to: str = "buyer@example.test", body: str = "Hello") -> RawMessage:
    return RawMessage(
        source_system="test",
        source_identifier=key,
        sent_at=BASE,
        thread_identifier=thread,
        rfc822_message_id=f"<{key}@test>",
        from_address=sender,
        from_name="Someone",
        to_addresses=(to,),
        subject="Re: pricing",
        body_text=body,
    )


def buyer(db):
    profile = BuyerProfile(id=1, email="buyer@example.test")
    db.add(profile)
    db.flush()
    return profile


def test_one_dealership_can_hold_several_domains(db):
    dealer = make_dealer(db, "Honda of Westport")
    learn_domain(db, dealer, "hondaofwestport.test", kind=DomainKind.PRIMARY)
    learn_domain(db, dealer, "westport-auto-group.test", kind=DomainKind.MANAGEMENT)
    learn_domain(db, dealer, "mail.crm-vendor.test", kind=DomainKind.CRM)

    assert {d.domain for d in dealer.domains} == {
        "hondaofwestport.test",
        "westport-auto-group.test",
        "mail.crm-vendor.test",
    }
    assert {d.kind for d in dealer.domains} == {"PRIMARY", "MANAGEMENT", "CRM"}


def test_mail_from_any_of_them_resolves_to_the_same_dealership(db):
    buyer(db)
    dealer = make_dealer(db, "Honda of Westport")
    for domain in ("hondaofwestport.test", "westport-auto-group.test"):
        learn_domain(db, dealer, domain)

    staff = ingest(db, message("chris@hondaofwestport.test", key="a", thread="<a@t>"))
    manager = ingest(db, message("gm@westport-auto-group.test", key="b", thread="<b@t>"))

    assert staff.dealer_id == dealer.id
    assert manager.dealer_id == dealer.id


def test_learning_the_same_domain_twice_is_a_no_op(db):
    dealer = make_dealer(db, "Honda of Westport")
    first = learn_domain(db, dealer, "hondaofwestport.test")
    second = learn_domain(db, dealer, "HondaOfWestport.test")
    assert first.id == second.id
    assert db.query(DealerDomain).count() == 1


def test_relearning_a_domain_can_upgrade_it_to_verified(db):
    dealer = make_dealer(db, "Honda of Westport")
    learn_domain(db, dealer, "hondaofwestport.test", verified=False)
    learn_domain(db, dealer, "hondaofwestport.test", verified=True)
    assert db.query(DealerDomain).one().verified is True


def test_a_domain_shared_by_two_rooftops_goes_to_review(db):
    """Guessing here would silently merge two negotiations."""
    buyer(db)
    make_dealer(db, "Group Store A", domains="autogroup.test")
    make_dealer(db, "Group Store B", domains="autogroup.test")

    result = ingest(db, message("sales@autogroup.test"))

    assert result.dealer_id is None
    assert result.needs_review is True
    assert "2 dealers" in " ".join(result.reasons)


def test_a_shared_sender_address_falls_through_to_the_thread(db):
    """A CRM mailbox writing for several stores is real; first-match is wrong."""
    buyer(db)
    westport = make_dealer(db, "Honda of Westport", domains="hondaofwestport.test")
    make_dealer(db, "Honda of Stamford", domains="hondaofstamford.test")

    shared = "bdc@shared-crm.test"
    make_contact(db, westport, "BDC", email=shared)
    make_contact(db, db.query(type(westport)).filter_by(name="Honda of Stamford").one(),
                 "BDC", email=shared)

    # Establish a thread that unambiguously belongs to Westport.
    ingest(db, message("chris@hondaofwestport.test", key="seed", thread="<wp@t>"))
    result = ingest(db, message(shared, key="via-crm", thread="<wp@t>"))

    assert result.dealer_id == westport.id


def test_the_alias_outranks_everything(db):
    """Whatever domain the CRM uses, the alias the buyer typed is theirs."""
    buyer(db)
    make_dealer(db, "Honda of Stamford", domains="hondaofstamford.test")
    westport = make_dealer(
        db, "Honda of Westport", inquiry_alias="buyer+dl-westport@example.test"
    )

    result = ingest(
        db,
        message(
            "anonymous@some-crm.test",
            to="buyer+dl-westport@example.test",
        ),
    )
    assert result.dealer_id == westport.id


def test_deleting_a_dealership_takes_its_domains_with_it(db):
    dealer = make_dealer(db, "Honda of Westport", domains=["a.test", "b.test"])
    assert db.query(DealerDomain).count() == 2
    db.delete(dealer)
    db.flush()
    assert db.query(DealerDomain).count() == 0


def test_domains_are_editable_through_the_api(client, db):
    created = client.post(
        "/api/dealers",
        json={"name": "Honda of Westport", "domains": ["hondaofwestport.test"]},
    ).json()
    assert [d["domain"] for d in created["domains"]] == ["hondaofwestport.test"]

    updated = client.patch(
        f"/api/dealers/{created['id']}",
        json={"domains": ["hondaofwestport.test", "westport-auto-group.test"]},
    ).json()
    assert sorted(d["domain"] for d in updated["domains"]) == [
        "hondaofwestport.test",
        "westport-auto-group.test",
    ]

    trimmed = client.patch(
        f"/api/dealers/{created['id']}", json={"domains": ["westport-auto-group.test"]}
    ).json()
    assert [d["domain"] for d in trimmed["domains"]] == ["westport-auto-group.test"]
