"""Offer versioning. History is the product — nothing is edited in place."""

from __future__ import annotations

from conftest import make_dealer, make_offer

from app.models import Offer
from app.services import offers as offer_service
from app.services import pricing
from app.services.money import to_cents


def test_versions_increment_and_chain(db):
    dealer = make_dealer(db)
    first = make_offer(db, dealer, selling_price_cents=to_cents("28090.00"))
    second = make_offer(db, dealer, selling_price_cents=to_cents("27329.42"), days=1)

    assert (first.version, second.version) == (1, 2)
    assert second.supersedes_offer_id == first.id


def test_only_one_offer_is_current_per_dealer(db):
    dealer = make_dealer(db)
    make_offer(db, dealer, selling_price_cents=to_cents("28090.00"))
    make_offer(db, dealer, selling_price_cents=to_cents("27329.42"), days=1)
    make_offer(db, dealer, selling_price_cents=to_cents("27000.00"), days=2)

    current = [o for o in db.query(Offer).all() if o.is_current]
    assert len(current) == 1
    assert current[0].version == 3


def test_earlier_offers_are_preserved_verbatim(db):
    """"What did Stamford originally quote?" must stay answerable."""
    dealer = make_dealer(db)
    make_offer(db, dealer, selling_price_cents=to_cents("28090.00"),
               quoted_otd_cents=to_cents("31306.22"))
    make_offer(db, dealer, selling_price_cents=to_cents("27329.42"),
               quoted_otd_cents=to_cents("30500.01"), days=1)

    history = offer_service.history(db, dealer.id)
    assert [o.version for o in history] == [1, 2]
    assert history[0].quoted_otd_cents == to_cents("31306.22")
    assert history[0].selling_price_cents == to_cents("28090.00")


def test_two_dealers_version_independently(db):
    a = make_dealer(db, "Dealer A")
    b = make_dealer(db, "Dealer B")
    make_offer(db, a, selling_price_cents=to_cents("28000.00"))
    first_b = make_offer(db, b, selling_price_cents=to_cents("27000.00"))
    second_a = make_offer(db, a, selling_price_cents=to_cents("27500.00"), days=1)

    assert first_b.version == 1
    assert second_a.version == 2


def test_add_on_total_always_matches_the_lines(db):
    dealer = make_dealer(db)
    offer = make_offer(
        db,
        dealer,
        selling_price_cents=to_cents("28000.00"),
        tax_cents=to_cents("1700.00"),
        lines=[
            {"kind": "ADD_ON", "name": "VIN Etching", "price_cents": to_cents("269.00")},
            {"kind": "ADD_ON", "name": "Wheel Locks", "price_cents": to_cents("129.00")},
            {"kind": "ADD_ON", "name": "Nitrogen", "price_cents": to_cents("199.00")},
            {"kind": "DEALER_FEE", "name": "Doc prep", "price_cents": to_cents("100.00")},
        ],
    )
    result = pricing.compute(offer)
    assert result.add_ons_total_cents == to_cents("597.00")
    assert result.dealer_fees_total_cents == to_cents("100.00")


def test_financing_conditions_left_unconfirmed_stay_null(db):
    """NULL and False are different answers, and the difference is the whole point."""
    dealer = make_dealer(db)
    offer = make_offer(
        db,
        dealer,
        selling_price_cents=to_cents("27779.00"),
        financing_required=True,
        financing_provider="Honda Financial",
        apr_bp=714,
        financing_term_months=84,
    )
    assert offer.financing_required is True
    assert offer.prepayment_penalty is None
    assert offer.discount_clawback is None
    assert offer.minimum_loan_months is None
