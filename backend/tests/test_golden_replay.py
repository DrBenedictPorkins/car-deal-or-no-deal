"""Replay of the reference negotiation against hand-authored expectations.

This is the integration test the whole ingestion layer exists to pass. The .eml corpus
goes in a message at a time, in the order it happened, and the negotiation has to come
back out: the right offers, the right states at the right moments, the right dealer
identified, and the automated messages told apart from the real ones.

Expected values live in ``manifest.json`` and were written from the original figures,
not generated from this code.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.fixtures import golden_corpus
from app.ingestion import eml
from app.ingestion.replay import ReplayRun, replay
from app.models import Contact, Interaction, Offer
from app.services import pricing

GOLDEN = Path(__file__).parent / "fixtures" / "golden"
MANIFEST = json.loads((GOLDEN / "manifest.json").read_text())


def message_key(interaction: Interaction) -> str:
    """The stable key from the fixture's Message-ID: "<t5-westport-quote@...>"."""
    return (interaction.source_identifier or "").strip("<>").split("@")[0]


@pytest.fixture(scope="module")
def corpus():
    messages = eml.load_directory(GOLDEN, source_system="replay")
    assert messages, "golden corpus is empty — run `python -m app.cli golden-build`"
    return messages


@pytest.fixture(scope="module")
def replayed(corpus):
    """Replay the corpus once for the whole module.

    Twenty-odd assertions against one negotiation should not mean twenty-odd replays;
    the tests below are read-only apart from the idempotency check, which is supposed
    to be a no-op anyway.
    """
    from app.db import SessionLocal, engine
    from app.models import Base
    from app.services.states import ensure_states

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    ensure_states(session)
    golden_corpus.seed_profile(session)
    golden_corpus.seed_dealers(session)
    session.flush()
    run = replay(session, corpus)
    session.commit()
    try:
        yield session, run
    finally:
        session.close()


# Shadows the per-test database from conftest: this module shares one replayed state.
@pytest.fixture()
def db(replayed):
    return replayed[0]


@pytest.fixture()
def run(replayed) -> ReplayRun:
    return replayed[1]


def checkpoint_for(run: ReplayRun, message_key_wanted: str):
    for point in run.checkpoints:
        if point.message_id.strip("<>").split("@")[0] == message_key_wanted:
            return point
    raise AssertionError(f"no checkpoint for {message_key_wanted}")


def interaction_for(db, key: str) -> Interaction:
    for row in db.query(Interaction).all():
        if message_key(row) == key:
            return row
    raise AssertionError(f"message {key} was not ingested")


# ------------------------------------------------------------------ ingestion


def test_every_message_is_ingested_exactly_once(run, corpus, db):
    assert sum(1 for r in run.results if r.created) == len(corpus)
    assert db.query(Interaction).count() == len(corpus)


def test_replaying_the_same_corpus_again_changes_nothing(db, corpus, run):
    before = (
        db.query(Interaction).count(),
        db.query(Offer).count(),
        db.query(Contact).count(),
    )
    second = replay(db, corpus)
    after = (
        db.query(Interaction).count(),
        db.query(Offer).count(),
        db.query(Contact).count(),
    )
    assert before == after
    assert all(result.duplicate for result in second.results)


def test_messages_are_attached_to_the_right_dealer(run, db):
    for key, expected in MANIFEST["messages"].items():
        interaction = interaction_for(db, key)
        if expected["dealer"] is None:
            assert interaction.dealer_id is None, f"{key} should not resolve to a dealer"
        else:
            assert interaction.dealer.name == expected["dealer"], key
        assert interaction.direction == expected["direction"], key


def test_gmail_style_threads_hold_a_conversation_together(run, db):
    """Every message in a thread must land on one dealer, not several."""
    threads: dict[str, set[int | None]] = {}
    for interaction in db.query(Interaction).all():
        threads.setdefault(interaction.source_thread_identifier, set()).add(
            interaction.dealer_id
        )
    for thread, dealer_ids in threads.items():
        assert len(dealer_ids) == 1, f"thread {thread} split across dealers {dealer_ids}"


def test_contacts_are_discovered_without_being_configured(run, db):
    """Only the dealerships are seeded; the people are found by ingestion."""
    by_name = {c.name: c for c in db.query(Contact).all()}
    for key, expected in MANIFEST["messages"].items():
        if "contact_name" not in expected:
            continue
        assert expected["contact_name"] in by_name, key
        contact = by_name[expected["contact_name"]]
        assert contact.dealer.name == expected["dealer"]
        assert contact.email


def test_two_people_at_one_dealership_are_kept_apart(run, db):
    kisco = [c for c in db.query(Contact).all() if c.dealer.name == "Mount Kisco Honda"]
    assert {c.name for c in kisco} == {"Amber Chase", "James Woods"}


def test_the_unattributable_blast_lands_in_the_review_queue(run, db):
    interaction = interaction_for(db, "t15-unrelated-blast")
    assert interaction.dealer_id is None
    assert interaction.needs_review is True


# -------------------------------------------------------------- classification


def test_automated_messages_are_told_apart_from_real_ones(run, db):
    for key, expected in MANIFEST["messages"].items():
        if "actor_kind" not in expected:
            continue
        interaction = interaction_for(db, key)
        assert interaction.actor_kind == expected["actor_kind"], key
        assert interaction.classification_reason, f"{key} has no stated reason"


def test_the_lead_system_follow_up_is_caught_by_its_restatement(run, db):
    interaction = interaction_for(db, "t4-tarrytown-corey-1")
    assert interaction.actor_kind == "AUTOMATED"
    assert "repeats the buyer's own inquiry" in interaction.classification_reason

    corey = next(c for c in db.query(Contact).all() if c.name == "Corey Smith")
    assert corey.actor_kind == "AUTOMATED"
    assert corey.automation_evidence


def test_a_genuine_reply_from_the_same_dealership_is_not_tarred_with_it(run, db):
    ebony = next(c for c in db.query(Contact).all() if c.name == "Ebony Bryant")
    assert ebony.dealer.name == "Tarrytown Honda"
    assert ebony.actor_kind != "AUTOMATED"


# ------------------------------------------------------------------ extraction


def _offer_for(db, key: str) -> Offer:
    interaction = interaction_for(db, key)
    offer = db.query(Offer).filter(Offer.interaction_id == interaction.id).one_or_none()
    assert offer is not None, f"no offer extracted from {key}"
    return offer


@pytest.mark.parametrize(
    "key", [k for k, v in MANIFEST["messages"].items() if v.get("offer")]
)
def test_extracted_offer_matches_the_golden_values(run, db, key):
    expected = MANIFEST["messages"][key]["offer"]
    offer = _offer_for(db, key)

    for field, value in expected.items():
        if field == "add_ons":
            continue
        assert getattr(offer, field) == value, f"{key}.{field}"

    actual_add_ons = sorted(
        (line.name, line.price_cents) for line in offer.lines if line.kind == "ADD_ON"
    )
    assert actual_add_ons == sorted(
        (a["name"], a["price_cents"]) for a in expected["add_ons"]
    ), key


@pytest.mark.parametrize(
    "key", [k for k, v in MANIFEST["messages"].items() if v.get("derived")]
)
def test_derived_pricing_matches_the_golden_values(run, db, key):
    expected = MANIFEST["messages"][key]["derived"]
    result = pricing.compute(_offer_for(db, key), expected_tax_rate_bp=600)
    for field, value in expected.items():
        assert getattr(result, field) == value, f"{key}.{field}"


def test_add_on_claims_made_away_from_the_line_items_are_still_captured(run, db):
    """"already on the car so I can't strip them" sits three lines below the figures."""
    offer = _offer_for(db, "t14-stamford-quote-2")
    for line in offer.lines:
        assert line.already_installed is True
        assert line.mandatory_claimed is True
        assert line.user_wants is False


def test_vehicle_facts_are_extracted_with_their_quotes(run, db):
    for key, expected in MANIFEST["messages"].items():
        for field, value in (expected.get("vehicle") or {}).items():
            offer = _offer_for(db, key)
            assert getattr(offer.vehicle_id and _vehicle(db, offer), field) == value, (
                f"{key}.{field}"
            )


def _vehicle(db, offer: Offer):
    from app.models import Vehicle

    return db.get(Vehicle, offer.vehicle_id)


def test_every_extracted_figure_is_traceable_to_its_quote(run, db):
    from app.models import Fact

    facts = (
        db.query(Fact)
        .filter(Fact.attribute.like("offer.%"), Fact.method == "RULE")
        .all()
    )
    assert facts, "extraction recorded no facts"
    for fact in facts:
        assert fact.interaction_id is not None, fact.attribute
        assert fact.quote, fact.attribute


# ------------------------------------------------------------------ timeline


def test_the_board_after_westports_quote(run):
    expected = MANIFEST["checkpoints"]["after_westport_quote"]
    point = checkpoint_for(run, expected["message"])
    assert point.best_otd_dealer == expected["best_otd_dealer"]
    assert point.best_otd_cents == expected["best_otd_cents"]
    for dealer, state in expected["dealer_states"].items():
        assert point.state_of(dealer) == state, dealer


def test_stamfords_opening_offer_does_not_displace_westport(run):
    expected = MANIFEST["checkpoints"]["after_stamford_first_quote"]
    point = checkpoint_for(run, expected["message"])

    assert point.best_otd_dealer == expected["best_otd_dealer"]
    assert point.best_otd_cents == expected["best_otd_cents"]
    assert point["Honda of Stamford"].otd_cents == 3130622

    for dealer, state in expected["dealer_states"].items():
        assert point.state_of(dealer) == state, dealer

    for dealer, codes in expected["friction"].items():
        for code in codes:
            assert point[dealer].has_friction(code), f"{dealer} missing {code}"


def test_the_revision_takes_the_lead(run):
    expected = MANIFEST["checkpoints"]["after_stamford_revision"]
    point = checkpoint_for(run, expected["message"])
    assert point.best_otd_dealer == expected["best_otd_dealer"]
    assert point.best_otd_cents == expected["best_otd_cents"]
    assert point["Honda of Stamford"].offer_count == expected["stamford_offer_versions"]
    assert point.state_of("Honda of Stamford") == "QUOTE_RECEIVED"


def test_offer_history_keeps_both_stamford_versions(run, db):
    offers = (
        db.query(Offer)
        .join(Offer.dealer)
        .filter(Offer.dealer.has(name="Honda of Stamford"))
        .order_by(Offer.version)
        .all()
    )
    assert [o.version for o in offers] == [1, 2]
    assert offers[0].quoted_otd_cents == 3130622
    assert offers[1].quoted_otd_cents == 3050001
    assert offers[1].supersedes_offer_id == offers[0].id
    assert offers[0].is_current is False and offers[1].is_current is True


# ------------------------------------------------------------------- outcomes


def test_the_negotiation_outcomes(run, db):
    expected = MANIFEST["outcomes"]
    offers = (
        db.query(Offer)
        .filter(Offer.dealer.has(name="Honda of Stamford"))
        .order_by(Offer.version)
        .all()
    )
    first, final = (pricing.compute(o) for o in offers)
    westport = pricing.compute(
        db.query(Offer).filter(Offer.dealer.has(name="Honda of Westport")).one()
    )

    assert (
        first.effective_otd_cents - final.effective_otd_cents
        == expected["stamford_improvement_cents"]
    )
    assert (
        westport.effective_otd_cents - final.effective_otd_cents
        == expected["stamford_beats_westport_otd_cents"]
    )
    assert (
        westport.dealer_controlled_cents - final.dealer_controlled_cents
        == expected["stamford_beats_westport_dealer_controlled_cents"]
    )
    assert final.discount_from_msrp_cents == expected["stamford_discount_from_msrp_cents"]


def test_the_cheapest_dealer_cost_is_not_the_cheapest_out_the_door(run):
    """Curry is cheapest on what the dealer controls and has no OTD at all."""
    expected = MANIFEST["outcomes"]
    final = run.final
    assert final.best_dealer_controlled_dealer == expected["cheapest_dealer_controlled_dealer"]
    assert final.best_dealer_controlled_cents == expected["cheapest_dealer_controlled_cents"]
    assert final.best_otd_dealer == "Honda of Stamford"
    assert final["Curry Honda"].otd_cents is None
