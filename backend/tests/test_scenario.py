"""The acceptance scenario.

Loading the real negotiation and running only deterministic passes must reconstruct
what actually happened. If any of these drift, the engine has stopped modelling reality.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.fixtures.honda_scenario import load
from app.services import comparison, dashboard, narrative, queries
from app.services import contradictions as contradiction_service
from app.services.context import build_context, build_contexts
from app.services.money import to_cents

BASE = datetime(2026, 8, 30, 9, 0, 0)


@pytest.fixture()
def scenario(db):
    ids = load(db, base_time=BASE, accepted=True)
    contradiction_service.detect_all(db)
    db.flush()
    return ids


def pricing_for(db, dealer_id):
    return build_context(db, dealer_id).current_pricing


def test_stamford_final_beats_westport_on_out_the_door(scenario, db):
    stamford = pricing_for(db, scenario["Honda of Stamford"])
    westport = pricing_for(db, scenario["Honda of Westport"])

    assert stamford.effective_otd_cents == to_cents("30500.01")
    assert westport.effective_otd_cents == to_cents("30775.10")
    assert westport.effective_otd_cents - stamford.effective_otd_cents == to_cents("275.09")


def test_stamford_also_wins_on_dealer_controlled_cost(scenario, db):
    stamford = pricing_for(db, scenario["Honda of Stamford"])
    westport = pricing_for(db, scenario["Honda of Westport"])

    assert stamford.dealer_controlled_cents == to_cents("28426.42")
    assert westport.dealer_controlled_cents == to_cents("28734.00")
    assert (
        westport.dealer_controlled_cents - stamford.dealer_controlled_cents
        == to_cents("307.58")
    )


def test_stamford_improved_its_own_opening_offer(scenario, db):
    ctx = build_context(db, scenario["Honda of Stamford"])
    history = ctx.offer_history
    assert [p.version for p in history] == [1, 2]
    assert history[0].effective_otd_cents == to_cents("31306.22")
    assert history[1].effective_otd_cents == to_cents("30500.01")
    assert (
        history[0].effective_otd_cents - history[1].effective_otd_cents == to_cents("806.21")
    )


def test_final_discount_from_msrp(scenario, db):
    stamford = pricing_for(db, scenario["Honda of Stamford"])
    assert stamford.msrp_cents == to_cents("29090.00")
    assert stamford.discount_from_msrp_cents == to_cents("1760.58")


def test_westports_quote_has_an_unexplained_hundred_dollars(scenario, db):
    westport = pricing_for(db, scenario["Honda of Westport"])
    assert westport.computed_otd_cents == to_cents("30675.10")
    assert westport.otd_variance_cents == to_cents("100.00")
    assert westport.otd_reconciled is False


def test_stamfords_numbers_reconcile_exactly(scenario, db):
    stamford = pricing_for(db, scenario["Honda of Stamford"])
    assert stamford.otd_variance_cents == 0
    assert stamford.implied_tax_rate_bp == 600  # PA 6%, to the basis point


def test_curry_withheld_the_out_the_door_number(scenario, db):
    """A cheap selling price with no tax line must not win on OTD."""
    curry = pricing_for(db, scenario["Curry Honda"])
    assert curry.dealer_controlled_cents == to_cents("27954.00")
    assert curry.effective_otd_cents is None


def test_currys_financing_conditions_are_unconfirmed_and_stay_that_way(scenario, db):
    ctx = build_context(db, scenario["Curry Honda"])
    offer = ctx.current_offer
    assert offer.financing_required is True
    assert offer.apr_bp == 714
    assert offer.financing_term_months == 84
    assert offer.prepayment_penalty is None
    assert offer.discount_clawback is None

    summary = narrative.summarize(ctx)
    assert "Do not plan on financing and paying it off immediately" in summary


def test_the_call_contradicts_the_written_quote(scenario, db):
    ctx = build_context(db, scenario["Honda of Stamford"])
    open_rows = ctx.open_contradictions
    assert len(open_rows) == 1
    row = open_rows[0]
    assert "$398.00" in row.summary
    assert "call" in row.detail_a
    assert "VIN Etching" in row.detail_b


def test_the_automated_follow_ups_are_marked_as_such(scenario, db):
    ctx = build_context(db, scenario["Tarrytown Honda"])
    automated = [i for i in ctx.inbound if i.actor_kind == "AUTOMATED"]
    assert len(automated) == 2
    assert all(i.classification_reason for i in automated)

    bots = [c for c in ctx.dealer.contacts if c.actor_kind == "AUTOMATED"]
    assert bots and bots[0].automation_evidence


def test_the_dashboard_answers_the_seven_questions(scenario, db):
    view = dashboard.build(db)
    s = view.summary

    assert s.dealers_total == 7                          # who have I contacted
    assert s.dealers_responded == 7                      # who responded
    assert s.dealers_silent == 0                         # who hasn't
    assert s.best_otd_cents == to_cents("30500.01")      # best offer
    assert s.best_otd_dealer == "Honda of Stamford"
    assert s.recent_changes                              # what changed recently
    assert s.you_owe_count + s.dealer_owes_count > 0     # who owes a response
    assert all(row.next_action for row in view.rows)     # what should I do next


def test_every_dashboard_row_explains_itself(scenario, db):
    for row in dashboard.build(db).rows:
        assert row.owes_reason
        assert row.next_action_detail


def test_comparison_names_four_separate_winners(scenario, db):
    result = comparison.compare(db)
    labels = {w.label: w for w in result.winners}
    assert set(labels) == {
        "Cheapest OTD",
        "Cheapest dealer-controlled cost",
        "Most convenient",
        "Cleanest offer",
    }
    assert labels["Cheapest OTD"].dealer_name == "Honda of Stamford"
    assert labels["Cheapest OTD"].value == "$30,500.01"
    # The cheapest OTD and the cheapest dealer-controlled cost genuinely disagree here.
    assert labels["Cheapest dealer-controlled cost"].dealer_name == "Curry Honda"
    assert any("$100.00" in note for note in result.notes)


def test_who_owes_me_a_response(scenario, db):
    answer = queries.owes_me(db)
    assert "Curry Honda" in answer.answer


def test_who_gave_a_written_otd(scenario, db):
    answer = queries.written_otd(db)
    names = [row["dealer"] for row in answer.rows]
    assert names == ["Honda of Stamford", "Honda of Westport"]


def test_who_added_vin_etching(scenario, db):
    answer = queries.add_on_by_name(db, "etch")
    assert len(answer.rows) == 2  # both Stamford versions
    assert all(row["name"] == "VIN Etching" for row in answer.rows)


def test_did_anyone_confirm_it_was_not_a_demo(scenario, db):
    answer = queries.demo_confirmations(db)
    assert "2 dealer(s) affirmatively said" in answer.answer
    assert all(row["quote"] for row in answer.rows)


def test_what_did_stamford_originally_quote(scenario, db):
    answer = queries.first_quote(db, scenario["Honda of Stamford"])
    assert answer.rows[0]["otd_cents"] == to_cents("31306.22")


def test_phone_pressure_is_detected(scenario, db):
    from app.models import BehaviorSignal
    from app.services import behavior

    for ctx in build_contexts(db).values():
        for payload in behavior.derive_signals(ctx):
            db.add(BehaviorSignal(**payload))
    db.flush()

    answer = queries.phone_pressure(db)
    assert "White Plains Honda" in answer.answer
    assert "Mount Kisco Honda" in answer.answer


def test_the_negotiation_summary_is_built_from_structured_state(scenario, db):
    summary = narrative.summarize(build_context(db, scenario["Honda of Stamford"]))
    assert "$30,500.01" in summary
    assert "$28,426.42" in summary
    assert "$1,760.58" in summary
    assert "$806.21" in summary


def test_loading_the_fixture_twice_is_not_required_to_be_idempotent(scenario, db):
    """Documenting the boundary: the fixture seeds, it does not merge."""
    from app.models import Dealer

    assert db.query(Dealer).count() == 7
