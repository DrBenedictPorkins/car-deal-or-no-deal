"""Outbound safety.

Three independent locks: the transport has to be enabled, safe mode has to permit the
recipient, and a human has to have approved the specific draft. These tests exist
because the failure mode is real email reaching a real dealership from a test run.
"""

from __future__ import annotations

import pytest
from conftest import make_contact, make_dealer

from app.config import Settings
from app.enums import DraftStatus
from app.ingestion.base import (
    NullTransport,
    RecordingTransport,
    SafeTransport,
    SendRefused,
    get_transport,
)
from app.ingestion.messages import OutboundMessage
from app.models import DraftMessage, EmailSource, Interaction
from app.services import outbound


def settings(**overrides) -> Settings:
    base = {
        "send_enabled": True,
        "send_safe_mode": True,
        "send_allowlist": ["test-dealer@example.test"],
    }
    base.update(overrides)
    return Settings(**base)


def outbound_message(to: str = "test-dealer@example.test") -> OutboundMessage:
    return OutboundMessage(to_addresses=(to,), subject="Pricing", body_text="Hello")


# ------------------------------------------------------------------ defaults


def test_sending_is_off_by_default():
    assert Settings().send_enabled is False
    assert Settings().send_safe_mode is True
    assert isinstance(get_transport(Settings()), NullTransport)


def test_the_null_transport_explains_itself():
    with pytest.raises(SendRefused, match="No outbound transport"):
        NullTransport().send(outbound_message())


def test_safe_mode_blocks_an_address_that_is_not_allowlisted():
    transport = SafeTransport(RecordingTransport(), settings())
    with pytest.raises(SendRefused, match="not in DEALBENCH_SEND_ALLOWLIST"):
        transport.send(outbound_message("sales@arealdealership.com"))


def test_safe_mode_allows_an_allowlisted_address():
    inner = RecordingTransport()
    receipt = SafeTransport(inner, settings()).send(outbound_message())
    assert len(inner.sent) == 1
    assert receipt.source_identifier


def test_a_blocked_cc_stops_the_whole_message():
    """One unlisted recipient is enough; partial sends are worse than none."""
    inner = RecordingTransport()
    transport = SafeTransport(inner, settings())
    message = OutboundMessage(
        to_addresses=("test-dealer@example.test",),
        cc_addresses=("someone@elsewhere.com",),
        subject="x",
        body_text="y",
    )
    with pytest.raises(SendRefused):
        transport.send(message)
    assert inner.sent == []


def test_allowlisting_matches_the_address_inside_a_display_name():
    transport = SafeTransport(RecordingTransport(), settings())
    transport.send(outbound_message("Test Dealer <test-dealer@example.test>"))


def test_turning_off_safe_mode_still_requires_sending_to_be_enabled():
    config = settings(send_enabled=False, send_safe_mode=False)
    allowed, reason = config.sending_allowed_to("anyone@anywhere.test")
    assert allowed is False
    assert "Sending is disabled" in reason


# ----------------------------------------------------------- draft lifecycle


@pytest.fixture()
def draft(db):
    dealer = make_dealer(db, "Test Honda")
    make_contact(db, dealer, "Pat Seller", email="test-dealer@example.test", is_primary=True)
    row = DraftMessage(dealer_id=dealer.id, subject="Pricing", body="Hello")
    db.add(row)
    db.flush()
    return row


def test_an_unapproved_draft_cannot_be_sent(db, draft):
    with pytest.raises(SendRefused, match="approve it explicitly"):
        outbound.send_draft(db, draft, transport=RecordingTransport())


def test_an_approved_draft_is_sent_and_recorded_as_an_interaction(db, draft):
    draft.status = DraftStatus.APPROVED
    db.flush()

    transport = RecordingTransport()
    outcome = outbound.send_draft(db, draft, transport=transport)

    assert len(transport.sent) == 1
    assert transport.sent[0].to_addresses == ("test-dealer@example.test",)
    assert outcome.draft.status == DraftStatus.SENT
    assert outcome.draft.sent_at is not None

    interaction = db.get(Interaction, outcome.interaction.id)
    assert interaction.direction == "OUTBOUND"
    assert interaction.source_identifier == outcome.provider_message_id
    source = db.get(EmailSource, interaction.id)
    assert source.provider_message_id == outcome.provider_message_id


def test_a_draft_cannot_be_sent_twice(db, draft):
    draft.status = DraftStatus.APPROVED
    db.flush()
    transport = RecordingTransport()
    outbound.send_draft(db, draft, transport=transport)
    with pytest.raises(SendRefused, match="already been sent"):
        outbound.send_draft(db, draft, transport=transport)
    assert len(transport.sent) == 1


def test_syncing_a_sent_message_back_does_not_duplicate_it(db, draft):
    """The Sent folder contains what we just sent; the pipeline must recognise it."""
    from datetime import datetime

    from app.ingestion.messages import RawMessage
    from app.ingestion.pipeline import ingest

    draft.status = DraftStatus.APPROVED
    db.flush()
    outcome = outbound.send_draft(db, draft, transport=RecordingTransport())
    before = db.query(Interaction).count()

    echoed = RawMessage(
        source_system="recording",
        source_identifier=outcome.provider_message_id,
        sent_at=datetime.now(),
        thread_identifier=outcome.provider_thread_id,
        from_address="buyer@example.test",
        to_addresses=("test-dealer@example.test",),
        subject="Pricing",
        body_text="Hello",
    )
    result = ingest(db, echoed)

    assert result.duplicate is True
    assert db.query(Interaction).count() == before


def test_a_draft_with_no_contact_address_is_refused(db):
    dealer = make_dealer(db, "Silent Honda")
    row = DraftMessage(dealer_id=dealer.id, subject="Pricing", body="Hello",
                       status=DraftStatus.APPROVED)
    db.add(row)
    db.flush()
    with pytest.raises(SendRefused, match="No email address on file"):
        outbound.send_draft(db, row, transport=RecordingTransport())


def test_the_api_refuses_to_set_sent_directly(client, db):
    dealer = make_dealer(db, "Test Honda")
    db.commit()
    created = client.post(
        "/api/drafts", json={"dealer_id": dealer.id, "body": "Hi", "subject": "Pricing"}
    ).json()
    response = client.patch(f"/api/drafts/{created['id']}", json={"status": "SENT"})
    assert response.status_code == 400
    assert "/api/ingest/drafts" in response.json()["detail"]


def test_the_send_endpoint_reports_the_refusal_reason(client, db):
    dealer = make_dealer(db, "Test Honda")
    make_contact(db, dealer, "Pat", email="test-dealer@example.test", is_primary=True)
    db.commit()
    created = client.post(
        "/api/drafts", json={"dealer_id": dealer.id, "body": "Hi", "subject": "Pricing"}
    ).json()
    client.patch(f"/api/drafts/{created['id']}", json={"status": "APPROVED"})

    response = client.post(f"/api/ingest/drafts/{created['id']}/send")
    assert response.status_code == 403
    # The default configuration has no transport at all, so that is the honest reason.
    assert "No outbound transport is configured" in response.json()["detail"]
