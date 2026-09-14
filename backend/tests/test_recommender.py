"""Next-action rules.

The advice is arithmetic, not vibes: whether to push a dealer depends on the gap to the
best competing written offer and on how much a local dealer is worth to the buyer.
"""

from __future__ import annotations

from datetime import timedelta

from conftest import BASE_TIME, make_dealer, make_interaction, make_offer

from app.enums import Direction
from app.services import recommender
from app.services.context import build_context, build_contexts
from app.services.money import to_cents

NOW = BASE_TIME + timedelta(days=3)


def _recommend(db, dealer, **kwargs):
    contexts = list(build_contexts(db, now=NOW).values())
    ctx = build_context(db, dealer.id, now=NOW)
    benchmark = recommender.best_benchmark(contexts, excluding_dealer_id=dealer.id)
    return recommender.recommend(ctx, benchmark, **kwargs)


def _quote(db, dealer, otd: str, days: float = 0):
    return make_offer(
        db,
        dealer,
        days=days,
        selling_price_cents=to_cents("28000.00"),
        tax_cents=to_cents("1680.00"),
        quoted_otd_cents=to_cents(otd),
    )


def test_untouched_dealer_is_told_to_make_contact(db, profile):
    dealer = make_dealer(db)
    rec = _recommend(db, dealer)
    assert rec.code == "SEND_INITIAL_INQUIRY"


def test_a_dealer_owing_a_reply_gets_a_follow_up_with_the_elapsed_time(db, profile):
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.INBOUND, days=0)
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=0.5)
    rec = _recommend(db, dealer)
    assert rec.code == "FOLLOW_UP"
    assert "2 days" in rec.headline


def test_a_recent_message_means_wait_rather_than_nag(db, profile):
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=2.9)
    rec = _recommend(db, dealer)
    assert rec.code == "WAIT"


def test_a_dealer_dodging_into_a_phone_call_gets_the_written_only_line(db, profile):
    dealer = make_dealer(db)
    make_interaction(db, dealer, direction=Direction.OUTBOUND, days=0)
    make_interaction(
        db, dealer, direction=Direction.INBOUND, days=1,
        body="Give me a call and I'll go over everything with you.",
    )
    from app.models import BehaviorSignal
    from app.services import behavior

    ctx = build_context(db, dealer.id, now=NOW)
    for payload in behavior.derive_signals(ctx):
        db.add(BehaviorSignal(**payload))
    db.flush()

    rec = _recommend(db, dealer)
    assert rec.code == "RESTATE_WRITTEN_ONLY"


def test_the_cheapest_dealer_is_told_to_lock_it_in(db, profile):
    winner = make_dealer(db, "Honda of Stamford")
    rival = make_dealer(db, "Honda of Westport")
    _quote(db, winner, "30500.01")
    _quote(db, rival, "30775.10")
    rec = _recommend(db, winner)
    assert rec.code == "LOCK_IN"
    assert "$275.09" in rec.detail


def test_a_more_expensive_dealer_is_asked_to_beat_the_written_number(db, profile):
    dealer = make_dealer(db, "Honda of Stamford", is_local=True)
    rival = make_dealer(db, "Honda of Westport")
    _quote(db, dealer, "31306.22")
    _quote(db, rival, "30775.10")

    rec = _recommend(db, dealer)
    assert rec.code == "ASK_TO_BEAT"
    assert "$30,775.10" in rec.headline
    assert "$531.12" in rec.detail
    assert "local" in rec.detail
    # And the draft is ready to send, citing the competing figure.
    assert "$30,775.10" in rec.suggested_message
    assert "buy locally" in rec.suggested_message


def test_a_small_local_premium_is_worth_paying(db, profile):
    """Buyer said a local dealer is worth $300; this one is $150 more expensive."""
    local = make_dealer(db, "Honda of Stamford", is_local=True)
    rival = make_dealer(db, "Honda of Westport")
    _quote(db, local, "30925.10")
    _quote(db, rival, "30775.10")

    rec = _recommend(db, local)
    assert rec.code == "ACCEPT_LOCAL_PREMIUM"
    assert "$150.00" in rec.headline


def test_a_premium_beyond_the_buyers_tolerance_is_still_pushed_back_on(db, profile):
    local = make_dealer(db, "Honda of Stamford", is_local=True)
    rival = make_dealer(db, "Honda of Westport")
    _quote(db, local, "31200.00")
    _quote(db, rival, "30775.10")
    assert _recommend(db, local).code == "ASK_TO_BEAT"


def test_a_lone_offer_has_nothing_to_benchmark_against(db, profile):
    dealer = make_dealer(db)
    _quote(db, dealer, "30500.01")
    rec = _recommend(db, dealer)
    assert rec.code == "PUSH_FIRST_OFFER"
    assert "only number on the table" in rec.detail


def test_an_expiring_offer_outranks_everything_else(db, profile):
    dealer = make_dealer(db)
    rival = make_dealer(db, "Rival")
    offer = _quote(db, dealer, "30900.00")
    _quote(db, rival, "30775.10")
    offer.expires_at = NOW + timedelta(hours=6)
    offer.deadline_note = "I need to know by tonight"
    db.flush()

    rec = _recommend(db, dealer)
    assert rec.code == "DEADLINE"
    assert rec.priority == 1
    assert "I need to know by tonight" in rec.detail


def test_an_open_contradiction_is_raised_before_price_talk(db, profile):
    from app.models import Contradiction

    dealer = make_dealer(db)
    rival = make_dealer(db, "Rival")
    _quote(db, dealer, "30900.00")
    _quote(db, rival, "30775.10")
    db.add(
        Contradiction(
            dealer_id=dealer.id,
            kind="PRESENCE_CONFLICT",
            summary="They said no add-ons, then quoted $398 of them.",
            dedupe_key="x",
        )
    )
    db.flush()
    rec = _recommend(db, dealer)
    assert rec.code == "RESOLVE_CONTRADICTION"


def test_once_a_deal_is_accepted_the_others_get_closed_out(db, profile):
    dealer = make_dealer(db)
    rival = make_dealer(db, "Rival")
    _quote(db, dealer, "30900.00")
    _quote(db, rival, "30775.10")
    rec = _recommend(db, dealer, deal_accepted_with="Honda of Stamford")
    assert rec.code == "CLOSE_OUT"
    assert "purchased from another dealership" in rec.suggested_message


def test_the_benchmark_prefers_an_offer_actually_in_writing(db, profile):
    """A cheaper verbal number is not leverage; a written one is."""
    verbal = make_dealer(db, "Verbal Honda")
    written = make_dealer(db, "Written Honda")
    make_offer(
        db, verbal, selling_price_cents=to_cents("27000.00"), tax_cents=to_cents("1620.00")
    )
    _quote(db, written, "30775.10")

    contexts = list(build_contexts(db, now=NOW).values())
    benchmark = recommender.best_benchmark(contexts, excluding_dealer_id=999)
    assert benchmark.dealer_name == "Written Honda"
    assert benchmark.has_written_otd is True


def test_every_recommendation_carries_the_numbers_it_used(db, profile):
    dealer = make_dealer(db, "Honda of Stamford", is_local=True)
    rival = make_dealer(db, "Honda of Westport")
    _quote(db, dealer, "31306.22")
    _quote(db, rival, "30775.10")
    rec = _recommend(db, dealer)
    assert rec.supporting_numbers["This dealer's OTD"] == "$31,306.22"
    assert "Best competing OTD (Honda of Westport)" in rec.supporting_numbers
