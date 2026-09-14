"""Provenance. Nothing is overwritten; every belief traces to a source."""

from __future__ import annotations

from conftest import make_dealer, make_interaction, make_vehicle

from app.enums import Direction, ExtractionMethod, FactStatus, SubjectType
from app.services import facts as fact_service


def test_a_fact_records_its_source_and_the_quote_that_established_it(db):
    dealer = make_dealer(db)
    vehicle = make_vehicle(db, dealer)
    interaction = make_interaction(
        db, dealer, direction=Direction.INBOUND, body="It is a new car, not a demo."
    )

    fact = fact_service.record(
        db,
        subject_type=SubjectType.VEHICLE,
        subject_id=vehicle.id,
        attribute="vehicle.is_demo",
        dealer_id=dealer.id,
        value_bool=False,
        interaction_id=interaction.id,
        quote="It is a new car, not a demo.",
    )
    assert fact.status == FactStatus.CURRENT
    assert fact.interaction_id == interaction.id
    assert fact.display_value == "No"

    provenance = fact_service.provenance(db, fact.id)
    assert provenance.interaction.id == interaction.id
    assert provenance.superseded == []


def test_a_changed_value_supersedes_rather_than_overwrites(db):
    dealer = make_dealer(db)
    vehicle = make_vehicle(db, dealer)
    first = make_interaction(db, dealer, days=0, body="16 miles")
    second = make_interaction(db, dealer, days=1, body="actually 210 miles")

    old = fact_service.record(
        db,
        subject_type=SubjectType.VEHICLE,
        subject_id=vehicle.id,
        attribute="vehicle.mileage",
        dealer_id=dealer.id,
        value_number=16,
        value_unit="miles",
        interaction_id=first.id,
        quote="16 miles",
    )
    new = fact_service.record(
        db,
        subject_type=SubjectType.VEHICLE,
        subject_id=vehicle.id,
        attribute="vehicle.mileage",
        dealer_id=dealer.id,
        value_number=210,
        value_unit="miles",
        interaction_id=second.id,
        quote="actually 210 miles",
    )

    db.refresh(old)
    assert old.status == FactStatus.SUPERSEDED
    assert old.superseded_by_id == new.id
    assert old.value_number == 16  # the original claim survives verbatim

    chain = fact_service.history(db, SubjectType.VEHICLE, vehicle.id, "vehicle.mileage")
    assert [f.value_number for f in chain] == [210, 16]


def test_re_recording_the_same_value_from_the_same_source_is_a_no_op(db):
    """Replaying an ingest must not churn the history."""
    dealer = make_dealer(db)
    vehicle = make_vehicle(db, dealer)
    interaction = make_interaction(db, dealer)

    kwargs = {
        "subject_type": SubjectType.VEHICLE,
        "subject_id": vehicle.id,
        "attribute": "vehicle.is_demo",
        "dealer_id": dealer.id,
        "value_bool": False,
        "interaction_id": interaction.id,
    }
    a = fact_service.record(db, **kwargs)
    b = fact_service.record(db, **kwargs)
    assert a.id == b.id
    assert len(fact_service.history(db, SubjectType.VEHICLE, vehicle.id, "vehicle.is_demo")) == 1


def test_the_same_value_from_a_different_source_is_corroboration_not_a_duplicate(db):
    dealer = make_dealer(db)
    vehicle = make_vehicle(db, dealer)
    call = make_interaction(db, dealer, days=0, channel="CALL")
    email = make_interaction(db, dealer, days=1)

    fact_service.record(
        db, subject_type=SubjectType.VEHICLE, subject_id=vehicle.id,
        attribute="vehicle.is_demo", dealer_id=dealer.id, value_bool=False,
        interaction_id=call.id,
    )
    fact_service.record(
        db, subject_type=SubjectType.VEHICLE, subject_id=vehicle.id,
        attribute="vehicle.is_demo", dealer_id=dealer.id, value_bool=False,
        interaction_id=email.id,
    )
    chain = fact_service.history(db, SubjectType.VEHICLE, vehicle.id, "vehicle.is_demo")
    assert len(chain) == 2
    assert chain[0].status == FactStatus.CURRENT
    assert chain[1].status == FactStatus.SUPERSEDED


def test_current_facts_exclude_superseded_ones(db):
    dealer = make_dealer(db)
    vehicle = make_vehicle(db, dealer)
    for miles in (16, 210):
        fact_service.record(
            db, subject_type=SubjectType.VEHICLE, subject_id=vehicle.id,
            attribute="vehicle.mileage", dealer_id=dealer.id, value_number=miles,
        )
    current = fact_service.current(db, SubjectType.VEHICLE, vehicle.id)
    assert len(current) == 1
    assert current[0].value_number == 210


def test_extraction_method_is_recorded_so_llm_claims_are_identifiable(db):
    dealer = make_dealer(db)
    fact = fact_service.record(
        db,
        subject_type=SubjectType.DEALER,
        subject_id=dealer.id,
        attribute="dealer.no_mandatory_add_ons",
        dealer_id=dealer.id,
        value_bool=True,
        method=ExtractionMethod.LLM,
        confidence=0.82,
    )
    assert fact.method == ExtractionMethod.LLM
    assert fact.confidence == 0.82
