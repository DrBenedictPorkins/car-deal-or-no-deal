"""Ingestion guards: duplicates, thread association, multiple contacts per dealer,
and automated-vs-human classification.

Phase 2 replaces the manual endpoint with the Gmail adapter, but the dedupe contract
tested here is the one the adapter has to satisfy.
"""

from __future__ import annotations

from conftest import make_contact, make_dealer

from app.enums import ActorKind
from app.models import Interaction


def _payload(dealer_id: int, **overrides) -> dict:
    base = {
        "dealer_id": dealer_id,
        "channel": "EMAIL",
        "direction": "INBOUND",
        "occurred_at": "2026-08-30T14:03:00",
        "subject": "Your Civic Sport Hatchback — numbers",
        "raw_content": "Selling price $28,090.00\nOut the door $31,306.22",
        "source_system": "gmail",
        "source_identifier": "msg-abc123",
        "source_thread_identifier": "thread-xyz",
    }
    base.update(overrides)
    return base


def test_the_same_message_ingested_twice_creates_one_interaction(client, db):
    dealer = make_dealer(db)
    db.commit()

    first = client.post("/api/interactions", json=_payload(dealer.id))
    second = client.post("/api/interactions", json=_payload(dealer.id))

    assert first.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert db.query(Interaction).count() == 1


def test_a_forwarded_copy_with_a_new_provider_id_is_still_a_duplicate(client, db):
    """Same content, different Gmail message id — one real message."""
    dealer = make_dealer(db)
    db.commit()

    client.post("/api/interactions", json=_payload(dealer.id))
    again = client.post("/api/interactions", json=_payload(dealer.id, source_identifier="msg-dup"))

    assert again.status_code == 201
    assert db.query(Interaction).count() == 1


def test_a_reused_provider_id_with_changed_content_is_still_one_message(client, db):
    dealer = make_dealer(db)
    db.commit()

    first = client.post("/api/interactions", json=_payload(dealer.id))
    second = client.post(
        "/api/interactions", json=_payload(dealer.id, raw_content="edited body")
    )
    assert second.json()["id"] == first.json()["id"]
    assert db.query(Interaction).count() == 1


def test_identical_boilerplate_from_two_dealers_is_two_messages(client, db):
    """Lead-management templates are near-identical across stores."""
    a = make_dealer(db, "Dealer A")
    b = make_dealer(db, "Dealer B")
    db.commit()

    client.post("/api/interactions", json=_payload(a.id, source_identifier="a-1"))
    client.post("/api/interactions", json=_payload(b.id, source_identifier="b-1"))
    assert db.query(Interaction).count() == 2


def test_thread_identifier_is_preserved_for_association(client, db):
    dealer = make_dealer(db)
    db.commit()

    client.post("/api/interactions", json=_payload(dealer.id))
    client.post(
        "/api/interactions",
        json=_payload(
            dealer.id,
            source_identifier="msg-def456",
            raw_content="Following up",
            occurred_at="2026-08-31T09:00:00",
        ),
    )
    rows = db.query(Interaction).all()
    assert len(rows) == 2
    assert {r.source_thread_identifier for r in rows} == {"thread-xyz"}


def test_one_dealer_can_have_several_contacts_with_different_classifications(client, db):
    """Tarrytown: a real client-services reply plus automated manager follow-ups."""
    dealer = make_dealer(db, "Tarrytown Honda")
    human = make_contact(db, dealer, "Ebony Bryant", actor_kind=ActorKind.HUMAN)
    bot = make_contact(
        db,
        dealer,
        "Corey Smith",
        actor_kind=ActorKind.AUTOMATED,
        automation_evidence="Restates the buyer's own inquiry verbatim on a fixed cadence.",
    )
    db.commit()

    client.post(
        "/api/interactions",
        json=_payload(dealer.id, contact_id=human.id, source_identifier="h-1",
                      raw_content="We'd need you to come in.", actor_kind="HUMAN"),
    )
    client.post(
        "/api/interactions",
        json=_payload(dealer.id, contact_id=bot.id, source_identifier="b-1",
                      raw_content="I see you were looking for a new 2026 Honda Civic...",
                      actor_kind="AUTOMATED",
                      classification_reason="Verbatim restatement of the buyer's inquiry."),
    )

    rows = db.query(Interaction).order_by(Interaction.id).all()
    assert [r.actor_kind for r in rows] == ["HUMAN", "AUTOMATED"]
    assert rows[1].classification_reason
    # The classification is justified, not asserted.
    assert bot.automation_evidence


def test_normalized_content_defaults_to_the_raw_body(client, db):
    dealer = make_dealer(db)
    db.commit()
    response = client.post("/api/interactions", json=_payload(dealer.id))
    assert response.json()["normalized_content"] == response.json()["raw_content"]


def test_ingesting_a_message_moves_the_dealer_state(client, db, profile):
    dealer = make_dealer(db)
    db.commit()
    assert dealer.state_code == "DISCOVERED"

    client.post("/api/interactions", json=_payload(dealer.id, direction="OUTBOUND"))
    db.refresh(dealer)
    assert dealer.state_code != "DISCOVERED"
