"""Contradiction detection.

The system shows both sides and never decides which is true.
"""

from __future__ import annotations

from datetime import timedelta

from conftest import BASE_TIME, make_dealer, make_interaction, make_offer, make_vehicle

from app.enums import (
    Channel,
    CommitmentStatus,
    ContradictionKind,
    Direction,
    Party,
    SubjectType,
)
from app.models import Commitment
from app.services import contradictions
from app.services import facts as fact_service
from app.services.context import build_context
from app.services.money import to_cents

NOW = BASE_TIME + timedelta(days=5)


def test_the_call_and_the_written_quote_disagree_about_add_ons(db, profile):
    """The canonical case: 2:14 PM call vs. 4:03 PM quote."""
    dealer = make_dealer(db, "Honda of Stamford")
    call = make_interaction(
        db,
        dealer,
        channel=Channel.CALL,
        direction=Direction.INBOUND,
        days=0,
        body="There are no mandatory dealer add-ons.",
    )
    fact_service.record(
        db,
        subject_type=SubjectType.DEALER,
        subject_id=dealer.id,
        attribute="dealer.no_mandatory_add_ons",
        dealer_id=dealer.id,
        value_bool=True,
        interaction_id=call.id,
        quote="There are no mandatory dealer add-ons.",
        observed_at=call.occurred_at,
    )
    quote = make_interaction(db, dealer, direction=Direction.INBOUND, days=0.1)
    make_offer(
        db,
        dealer,
        days=0.1,
        interaction_id=quote.id,
        selling_price_cents=to_cents("28090.00"),
        tax_cents=to_cents("1751.22"),
        lines=[
            {"kind": "ADD_ON", "name": "VIN Etching", "price_cents": to_cents("269.00"),
             "mandatory_claimed": True},
            {"kind": "ADD_ON", "name": "Wheel Locks", "price_cents": to_cents("129.00"),
             "mandatory_claimed": True},
        ],
    )

    found = contradictions.detect_for_dealer(db, build_context(db, dealer.id, now=NOW))
    presence = [c for c in found if c.kind == ContradictionKind.PRESENCE_CONFLICT]
    assert len(presence) == 1
    row = presence[0]
    assert "$398.00" in row.summary
    # Both sides are cited, and no resolution is proposed.
    assert "no mandatory add-ons" in row.detail_a
    assert "VIN Etching" in row.detail_b
    assert row.status == "OPEN"
    assert row.resolution_note is None


def test_a_denial_made_after_the_quote_is_not_a_contradiction(db, profile):
    """Saying "we'll take them off" later is agreement, not a conflict."""
    dealer = make_dealer(db)
    quote = make_interaction(db, dealer, direction=Direction.INBOUND, days=0)
    make_offer(
        db, dealer, days=0, interaction_id=quote.id,
        selling_price_cents=to_cents("28000.00"), tax_cents=to_cents("1680.00"),
        lines=[{"kind": "ADD_ON", "name": "Nitrogen", "price_cents": to_cents("199.00")}],
    )
    later = make_interaction(db, dealer, direction=Direction.INBOUND, days=1)
    fact_service.record(
        db, subject_type=SubjectType.DEALER, subject_id=dealer.id,
        attribute="dealer.no_mandatory_add_ons", dealer_id=dealer.id, value_bool=True,
        interaction_id=later.id, observed_at=later.occurred_at,
    )
    found = contradictions.detect_for_dealer(db, build_context(db, dealer.id, now=NOW))
    assert [c for c in found if c.kind == ContradictionKind.PRESENCE_CONFLICT] == []


def test_two_answers_about_demo_status_conflict(db, profile):
    dealer = make_dealer(db)
    vehicle = make_vehicle(db, dealer)
    first = make_interaction(db, dealer, days=0)
    second = make_interaction(db, dealer, days=1)
    for interaction, value in ((first, False), (second, True)):
        fact_service.record(
            db, subject_type=SubjectType.VEHICLE, subject_id=vehicle.id,
            attribute="vehicle.is_demo", dealer_id=dealer.id, value_bool=value,
            interaction_id=interaction.id, observed_at=interaction.occurred_at,
        )
    found = contradictions.detect_for_dealer(db, build_context(db, dealer.id, now=NOW))
    conflicts = [c for c in found if c.kind == ContradictionKind.VALUE_CONFLICT]
    assert len(conflicts) == 1
    assert "demo status" in conflicts[0].summary
    assert conflicts[0].fact_a_id and conflicts[0].fact_b_id


def test_a_negotiated_price_change_is_not_a_contradiction(db, profile):
    """Prices are supposed to move. Only immutable attributes conflict."""
    dealer = make_dealer(db)
    make_offer(db, dealer, days=0, selling_price_cents=to_cents("28090.00"),
               tax_cents=to_cents("1685.40"))
    make_offer(db, dealer, days=1, selling_price_cents=to_cents("27329.42"),
               tax_cents=to_cents("1639.77"))
    found = contradictions.detect_for_dealer(db, build_context(db, dealer.id, now=NOW))
    assert found == []


def test_a_price_moving_the_wrong_way_is_flagged(db, profile):
    dealer = make_dealer(db)
    make_offer(db, dealer, days=0, selling_price_cents=to_cents("28000.00"),
               tax_cents=to_cents("1680.00"))
    make_offer(db, dealer, days=1, selling_price_cents=to_cents("28400.00"),
               tax_cents=to_cents("1704.00"))
    found = contradictions.detect_for_dealer(db, build_context(db, dealer.id, now=NOW))
    numeric = [c for c in found if c.kind == ContradictionKind.NUMERIC_CONFLICT]
    assert len(numeric) == 1
    assert "$400.00" in numeric[0].summary


def test_an_overdue_promise_becomes_a_broken_commitment(db, profile):
    dealer = make_dealer(db)
    interaction = make_interaction(db, dealer, days=0)
    db.add(
        Commitment(
            dealer_id=dealer.id,
            party=Party.DEALER,
            interaction_id=interaction.id,
            text="I'll email the out-the-door number tonight.",
            due_at=BASE_TIME + timedelta(days=0.5),
            status=CommitmentStatus.OPEN,
        )
    )
    db.flush()
    found = contradictions.detect_for_dealer(db, build_context(db, dealer.id, now=NOW))
    broken = [c for c in found if c.kind == ContradictionKind.PROMISE_BROKEN]
    assert len(broken) == 1
    assert "overdue" in broken[0].summary


def test_a_promise_kept_is_not_flagged(db, profile):
    dealer = make_dealer(db)
    interaction = make_interaction(db, dealer, days=0)
    db.add(
        Commitment(
            dealer_id=dealer.id,
            party=Party.DEALER,
            interaction_id=interaction.id,
            text="I'll email the numbers tonight.",
            due_at=BASE_TIME + timedelta(days=0.5),
            status=CommitmentStatus.OPEN,
        )
    )
    make_interaction(db, dealer, direction=Direction.INBOUND, days=0.6)
    db.flush()
    found = contradictions.detect_for_dealer(db, build_context(db, dealer.id, now=NOW))
    assert [c for c in found if c.kind == ContradictionKind.PROMISE_BROKEN] == []


def test_detection_is_idempotent(db, profile):
    dealer = make_dealer(db)
    vehicle = make_vehicle(db, dealer)
    for offset, value in ((0, False), (1, True)):
        interaction = make_interaction(db, dealer, days=offset)
        fact_service.record(
            db, subject_type=SubjectType.VEHICLE, subject_id=vehicle.id,
            attribute="vehicle.is_demo", dealer_id=dealer.id, value_bool=value,
            interaction_id=interaction.id, observed_at=interaction.occurred_at,
        )
    ctx = build_context(db, dealer.id, now=NOW)
    first = contradictions.detect_for_dealer(db, ctx)
    second = contradictions.detect_for_dealer(db, ctx)
    assert {c.id for c in first} == {c.id for c in second}
