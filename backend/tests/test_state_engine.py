"""Negotiation state rules and the transition log."""

from __future__ import annotations

from datetime import timedelta

from conftest import BASE_TIME, make_dealer, make_offer

from app.enums import Direction
from app.models import StateTransition
from app.services import state_engine
from app.services.context import build_context
from app.services.money import to_cents
from tests.conftest import make_interaction

NOW = BASE_TIME + timedelta(days=10)


def _evaluate(db, dealer, now=NOW):
    return state_engine.evaluate(build_context(db, dealer.id, now=now))


def test_dealer_with_no_contact_is_discovered(db, profile):
    dealer = make_dealer(db)
    assert _evaluate(db, dealer).state == "DISCOVERED"


def test_just_contacted_is_not_yet_overdue(db, profile):
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=9.5)
    assert _evaluate(db, dealer).state == "CONTACTED"


def test_silence_past_the_follow_up_threshold_awaits_a_response(db, profile):
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=7)
    assert _evaluate(db, dealer).state == "AWAITING_RESPONSE"


def test_prolonged_silence_becomes_no_response(db, profile):
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=0)
    outcome = _evaluate(db, dealer)
    assert outcome.state == "NO_RESPONSE"
    assert "10 days" in outcome.reason_text


def test_reply_without_a_price_is_responded(db, profile):
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=8)
    make_interaction(db, dealer, direction=Direction.INBOUND, days=9)
    assert _evaluate(db, dealer).state == "RESPONDED"


def test_buyer_chasing_a_price_is_awaiting_quote(db, profile):
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=8)
    make_interaction(db, dealer, direction=Direction.INBOUND, days=8.5)
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=9)
    assert _evaluate(db, dealer).state == "AWAITING_QUOTE"


def test_an_offer_alone_proves_the_dealer_engaged(db, profile):
    """A quote that arrived off-channel still counts as a response."""
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=0)
    make_offer(db, dealer, days=1, selling_price_cents=to_cents("28000.00"))
    assert _evaluate(db, dealer).state == "QUOTE_RECEIVED"


def test_a_priced_offer_is_quote_received(db, profile):
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=8)
    make_interaction(db, dealer, direction=Direction.INBOUND, days=9)
    make_offer(db, dealer, days=9, selling_price_cents=to_cents("28000.00"))
    assert _evaluate(db, dealer).state == "QUOTE_RECEIVED"


def test_replying_after_an_offer_is_a_counter(db, profile):
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.INBOUND, days=9)
    make_offer(db, dealer, days=9, selling_price_cents=to_cents("28000.00"))
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=9.5)
    assert _evaluate(db, dealer).state == "COUNTER_SENT"


def test_an_unanswered_counter_becomes_awaiting_counter(db, profile):
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.INBOUND, days=2)
    make_offer(db, dealer, days=2, selling_price_cents=to_cents("28000.00"))
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=3)
    assert _evaluate(db, dealer).state == "AWAITING_COUNTER"


def test_terminal_states_are_the_users_judgement_not_the_engines(db, profile):
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=0)
    state_engine.set_state_manually(db, dealer, "ACCEPTED", reason="done")
    assert _evaluate(db, dealer) is None
    state_engine.apply(db, build_context(db, dealer.id, now=NOW))
    assert dealer.state_code == "ACCEPTED"


def test_a_pinned_state_is_not_overridden_but_the_disagreement_is_logged(db, profile):
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=9.5)
    state_engine.set_state_manually(db, dealer, "FINALIST", reason="my call", pin=True)

    state_engine.apply(db, build_context(db, dealer.id, now=NOW))
    assert dealer.state_code == "FINALIST"

    suppressed = [
        t for t in db.query(StateTransition).all() if t.was_suppressed_by_pin
    ]
    assert len(suppressed) == 1
    assert suppressed[0].to_state == "CONTACTED"


def test_unpinning_hands_the_dealer_back_to_the_rules(db, profile):
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=9.5)
    state_engine.set_state_manually(db, dealer, "FINALIST", pin=True)
    dealer.state_is_pinned = False
    db.flush()
    state_engine.apply(db, build_context(db, dealer.id, now=NOW))
    assert dealer.state_code == "CONTACTED"


def test_every_change_is_recorded_with_a_reason(db, profile):
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=9.5)
    state_engine.apply(db, build_context(db, dealer.id, now=NOW))
    transitions = db.query(StateTransition).all()
    assert len(transitions) == 1
    assert transitions[0].from_state == "DISCOVERED"
    assert transitions[0].to_state == "CONTACTED"
    assert transitions[0].reason_text
    assert transitions[0].rule_id == "never_replied"


def test_re_evaluating_an_unchanged_dealer_writes_nothing(db, profile):
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=9.5)
    state_engine.apply(db, build_context(db, dealer.id, now=NOW))
    state_engine.apply(db, build_context(db, dealer.id, now=NOW))
    assert db.query(StateTransition).count() == 1


def test_states_are_data_so_a_new_one_can_be_added_without_a_migration(db):
    from app.models import NegotiationStateDef

    db.add(
        NegotiationStateDef(
            code="WALKED_IN",
            label="Walked in",
            description="Visited the showroom in person.",
            sort_order=95,
            is_builtin=False,
        )
    )
    db.flush()
    dealer = make_dealer(db)
    state_engine.set_state_manually(db, dealer, "WALKED_IN")
    assert dealer.state_code == "WALKED_IN"
