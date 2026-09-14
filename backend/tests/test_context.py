"""Derived communication state: who owes a response, and how stale is this."""

from __future__ import annotations

from datetime import timedelta

from conftest import BASE_TIME, make_dealer, make_interaction, make_offer

from app.enums import Direction, Party, QuestionStatus
from app.models import Question
from app.services import state_engine
from app.services.context import build_context
from app.services.money import to_cents

NOW = BASE_TIME + timedelta(days=5)


def test_no_contact_means_the_buyer_moves_first(db, profile):
    dealer = make_dealer(db)
    party, reason = build_context(db, dealer.id, now=NOW).owes_response()
    assert party == Party.BUYER
    assert "No contact" in reason


def test_dealer_owes_after_the_buyer_writes_last(db, profile):
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.INBOUND, days=1)
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=2)
    party, _ = build_context(db, dealer.id, now=NOW).owes_response()
    assert party == Party.DEALER


def test_buyer_owes_after_the_dealer_writes_last(db, profile):
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=1)
    make_interaction(db, dealer, direction=Direction.INBOUND, days=2)
    party, _ = build_context(db, dealer.id, now=NOW).owes_response()
    assert party == Party.BUYER


def test_an_unanswered_question_outranks_message_order(db, profile):
    """A dealer who replies without answering still owes an answer."""
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=1)
    make_interaction(db, dealer, direction=Direction.INBOUND, days=2)
    db.add(
        Question(
            dealer_id=dealer.id,
            asked_by=Party.BUYER,
            text="Is there a prepayment penalty?",
            status=QuestionStatus.OPEN,
        )
    )
    db.flush()
    party, reason = build_context(db, dealer.id, now=NOW).owes_response()
    assert party == Party.DEALER
    assert "unanswered" in reason


def test_an_answered_question_stops_counting(db, profile):
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=1)
    make_interaction(db, dealer, direction=Direction.INBOUND, days=2)
    db.add(
        Question(
            dealer_id=dealer.id,
            asked_by=Party.BUYER,
            text="Is it a demo?",
            status=QuestionStatus.ANSWERED,
        )
    )
    db.flush()
    party, _ = build_context(db, dealer.id, now=NOW).owes_response()
    assert party == Party.BUYER


def test_nobody_owes_once_the_negotiation_is_terminal(db, profile):
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=1)
    state_engine.set_state_manually(db, dealer, "ACCEPTED")
    party, _ = build_context(db, dealer.id, now=NOW).owes_response()
    assert party == Party.NOBODY


def test_idle_time_measures_from_the_most_recent_message(db, profile):
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=0)
    make_interaction(db, dealer, direction=Direction.INBOUND, days=3)
    ctx = build_context(db, dealer.id, now=NOW)
    assert ctx.idle_days == 2.0


def test_internal_notes_do_not_reset_the_idle_clock(db, profile):
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=0)
    make_interaction(db, dealer, direction=Direction.INTERNAL, days=4, channel="NOTE")
    ctx = build_context(db, dealer.id, now=NOW)
    assert ctx.idle_days == 5.0


def test_current_offer_is_the_latest_version(db, profile):
    dealer = make_dealer(db)
    make_offer(db, dealer, selling_price_cents=to_cents("28090.00"))
    make_offer(db, dealer, selling_price_cents=to_cents("27329.42"), days=1)
    ctx = build_context(db, dealer.id, now=NOW)
    assert ctx.current_offer.version == 2
    assert [p.version for p in ctx.offer_history] == [1, 2]


def test_written_otd_is_distinguished_from_a_derived_one(db, profile):
    dealer = make_dealer(db)
    make_offer(
        db, dealer, selling_price_cents=to_cents("28000.00"), tax_cents=to_cents("1680.00")
    )
    assert build_context(db, dealer.id, now=NOW).has_written_otd is False
    make_offer(
        db,
        dealer,
        days=1,
        selling_price_cents=to_cents("28000.00"),
        tax_cents=to_cents("1680.00"),
        quoted_otd_cents=to_cents("29680.00"),
    )
    assert build_context(db, dealer.id, now=NOW).has_written_otd is True
