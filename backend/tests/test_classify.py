"""Human or machine, and why."""

from __future__ import annotations

from datetime import datetime

from app.enums import ActorKind
from app.ingestion.classify import classify, restatement_ratio
from app.ingestion.messages import RawMessage

BUYER_INQUIRY = """I'm shopping for a new 2026 Honda Civic Hatchback Sport (non-hybrid),
in a dark or subdued colour — no white. I'm paying cash and registering in Pennsylvania
with no trade-in. I'd like an itemized out-the-door price in writing for a specific VIN:
selling price, every dealer fee, any add-ons, plus tax and registration as separate
lines."""


def message(sender: str, name: str | None = "Pat Seller") -> RawMessage:
    return RawMessage(
        source_system="test",
        source_identifier="m1",
        sent_at=datetime(2026, 8, 30, 12, 0),
        from_address=sender,
        from_name=name,
    )


def test_a_no_reply_address_is_automated():
    result = classify(message("no-reply@dealer.example"), "Thanks for your interest!")
    assert result.actor_kind == ActorKind.AUTOMATED
    assert "no-reply" in result.reason


def test_a_lead_management_domain_is_automated():
    result = classify(message("sales@leadmanager.example"), "Hi there!")
    assert result.actor_kind == ActorKind.AUTOMATED
    assert "lead-management" in result.reason


def test_the_buyers_own_inquiry_played_back_is_automated():
    """The real case: signed by a Sales Manager, from the dealership's own domain."""
    body = (
        "I see you were looking for a new 2026 Honda Civic Hatchback Sport "
        "(non-hybrid), in a dark or subdued colour — no white, paying cash and "
        "registering in Pennsylvania with no trade-in, and that you'd like an itemized "
        "out-the-door price in writing for a specific VIN: selling price, every dealer "
        "fee, any add-ons, plus tax and registration as separate lines.\n\n"
        "I'd love to help! When can you come in?"
    )
    result = classify(
        message("corey.smith@tarrytownhonda.example"),
        body,
        buyer_texts=[BUYER_INQUIRY],
    )
    assert result.actor_kind == ActorKind.AUTOMATED
    assert "repeats the buyer's own inquiry" in result.reason


def test_the_same_template_sent_twice_is_automated():
    body = "Just checking in to see if you are still in the market for a vehicle today."
    result = classify(
        message("sales@dealer.example"),
        body,
        prior_dealer_texts=[body],
    )
    assert result.actor_kind == ActorKind.AUTOMATED
    assert "identical to an earlier message" in result.reason


def test_a_real_reply_with_numbers_and_a_named_sender_is_human():
    body = (
        "I took this to my GM. Selling price $27,329.42, doc fee $699.00. "
        "VIN 19XFL2H81TE040705, 10 miles."
    )
    result = classify(
        message("edwin.sanchez@stamford.example", "Edwin Sanchez"),
        body,
        buyer_texts=[BUYER_INQUIRY],
    )
    assert result.actor_kind == ActorKind.HUMAN
    assert result.reason


def test_a_bland_reply_is_unknown_rather_than_guessed_at():
    result = classify(message("someone@dealer.example", None), "Thanks! Talk soon.")
    assert result.actor_kind == ActorKind.UNKNOWN


def test_every_verdict_carries_a_reason():
    for sender, body in (
        ("no-reply@x.example", "hi"),
        ("a@dealer.example", "Selling price $28,000 VIN 123"),
        ("b@dealer.example", "ok"),
    ):
        assert classify(message(sender), body).reason


def test_restatement_ratio_ignores_ordinary_shared_vocabulary():
    """Two messages about the same car are not the same message."""
    ratio = restatement_ratio(
        "The Civic Sport Hatchback is available in Meteorite Gray with 10 miles.",
        BUYER_INQUIRY,
    )
    assert ratio < 0.35
