"""Pricing arithmetic — the numbers the whole application ranks on.

Every expected value here comes from the real negotiation, so a regression in
normalization shows up as a disagreement with what actually happened.
"""

from __future__ import annotations

import pytest
from conftest import make_dealer, make_offer

from app.services import pricing
from app.services.money import fmt, to_cents


@pytest.fixture()
def westport(db):
    """The clean written benchmark — and the offer with the $100 discrepancy."""
    dealer = make_dealer(db, "Honda of Westport")
    return make_offer(
        db,
        dealer,
        selling_price_cents=to_cents("28035.00"),
        doc_fee_cents=to_cents("699.00"),
        registration_cents=to_cents("253.00"),
        tax_cents=to_cents("1688.10"),
        quoted_otd_cents=to_cents("30775.10"),
    )


@pytest.fixture()
def stamford_final(db):
    dealer = make_dealer(db, "Honda of Stamford")
    return make_offer(
        db,
        dealer,
        msrp_cents=to_cents("29090.00"),
        selling_price_cents=to_cents("27329.42"),
        doc_fee_cents=to_cents("699.00"),
        other_non_tax_fees_cents=to_cents("368.00"),
        tax_cents=to_cents("1705.59"),
        quoted_otd_cents=to_cents("30500.01"),
        lines=[
            {"kind": "ADD_ON", "name": "VIN Etching", "price_cents": to_cents("269.00"),
             "is_taxable": True, "user_wants": False},
            {"kind": "ADD_ON", "name": "Wheel Locks", "price_cents": to_cents("129.00"),
             "is_taxable": True, "user_wants": False},
        ],
    )


def test_dealer_controlled_excludes_government_charges(westport):
    result = pricing.compute(westport)
    # selling 28,035 + doc fee 699 — tax and registration are not the dealer's to move.
    assert result.dealer_controlled_cents == to_cents("28734.00")
    assert result.government_total_cents == to_cents("1941.10")


def test_add_ons_are_summed_from_lines_not_a_stored_total(stamford_final):
    result = pricing.compute(stamford_final)
    assert result.add_ons_total_cents == to_cents("398.00")
    assert result.dealer_controlled_cents == to_cents("28426.42")


def test_computed_otd_matches_quoted_when_the_dealer_is_straight(stamford_final):
    result = pricing.compute(stamford_final)
    assert result.computed_otd_cents == to_cents("30500.01")
    assert result.otd_variance_cents == 0
    assert result.otd_reconciled is True
    assert result.warnings == []


def test_otd_reconciliation_surfaces_an_unexplained_charge(westport):
    result = pricing.compute(westport)
    assert result.computed_otd_cents == to_cents("30675.10")
    assert result.quoted_otd_cents == to_cents("30775.10")
    assert result.otd_variance_cents == to_cents("100.00")
    assert result.otd_reconciled is False
    assert "unexplained" in result.warnings[0]


def test_ranking_uses_the_quoted_otd_the_buyer_would_actually_pay(westport):
    result = pricing.compute(westport)
    assert result.effective_otd_cents == to_cents("30775.10")


def test_stamford_beats_westport_by_the_real_margin(westport, stamford_final):
    w = pricing.compute(westport)
    s = pricing.compute(stamford_final)
    assert w.effective_otd_cents - s.effective_otd_cents == to_cents("275.09")
    assert w.dealer_controlled_cents - s.dealer_controlled_cents == to_cents("307.58")


def test_discount_from_msrp(stamford_final):
    assert pricing.compute(stamford_final).discount_from_msrp_cents == to_cents("1760.58")


def test_implied_tax_rate_matches_pennsylvania(stamford_final):
    result = pricing.compute(stamford_final, expected_tax_rate_bp=600)
    # 27,329.42 selling + 699 doc fee + 398 add-ons, taxed at 6% = 1,705.59
    assert result.taxable_base_cents == to_cents("28426.42")
    assert result.implied_tax_rate_bp == 600
    assert result.tax_rate_variance_bp == 0


def test_implied_tax_rate_flags_a_mismatched_base(westport):
    result = pricing.compute(westport, expected_tax_rate_bp=600)
    # Westport taxed a base $100 higher than the line items disclose.
    assert result.implied_tax_rate_bp != 600
    assert any("tax" in w.lower() for w in result.warnings)


def test_clean_offer_strips_unwanted_removable_add_ons(stamford_final):
    result = pricing.compute(stamford_final)
    assert result.unwanted_add_ons_cents == to_cents("398.00")
    assert result.clean_dealer_controlled_cents == to_cents("28028.42")
    assert result.clean_otd_cents == to_cents("30102.01")


def test_add_ons_confirmed_unremovable_are_not_stripped(db):
    dealer = make_dealer(db, "Sticky Honda")
    offer = make_offer(
        db,
        dealer,
        selling_price_cents=to_cents("28000.00"),
        tax_cents=to_cents("1700.00"),
        lines=[
            {"kind": "ADD_ON", "name": "Ceramic Coating", "price_cents": to_cents("995.00"),
             "user_wants": False, "removable_confirmed": False},
        ],
    )
    result = pricing.compute(offer)
    assert result.unwanted_add_ons_cents == 0


def test_no_tax_data_means_no_derivable_otd(db):
    """A dealer who withholds the tax line must not appear cheapest on OTD."""
    dealer = make_dealer(db, "Curry Honda")
    offer = make_offer(
        db,
        dealer,
        selling_price_cents=to_cents("27779.00"),
        doc_fee_cents=to_cents("175.00"),
    )
    result = pricing.compute(offer)
    assert result.dealer_controlled_cents == to_cents("27954.00")
    assert result.computed_otd_cents is None
    assert result.effective_otd_cents is None
    assert any("out-the-door" in w for w in result.warnings)


def test_missing_selling_price_is_a_warning_not_a_crash(db):
    dealer = make_dealer(db, "Vague Honda")
    offer = make_offer(db, dealer, tax_cents=to_cents("1000.00"))
    result = pricing.compute(offer)
    assert result.dealer_controlled_cents is None
    assert result.is_complete is False
    assert "No selling price on this offer." in result.warnings


def test_fee_flagged_as_pass_through_moves_to_the_government_side(db):
    """Assumption A1 is overridable per line."""
    dealer = make_dealer(db, "Honest Honda")
    offer = make_offer(
        db,
        dealer,
        selling_price_cents=to_cents("28000.00"),
        tax_cents=to_cents("1680.00"),
        lines=[
            {"kind": "DEALER_FEE", "name": "State inspection", "price_cents": to_cents("50.00"),
             "is_dealer_controlled": False},
        ],
    )
    result = pricing.compute(offer)
    assert result.dealer_controlled_cents == to_cents("28000.00")
    assert result.government_total_cents == to_cents("1730.00")


def test_progression_shows_how_far_the_dealer_moved(db):
    dealer = make_dealer(db, "Honda of Stamford")
    first = make_offer(
        db, dealer, days=0,
        msrp_cents=to_cents("29090.00"),
        selling_price_cents=to_cents("28090.00"),
        doc_fee_cents=to_cents("699.00"),
        other_non_tax_fees_cents=to_cents("368.00"),
        tax_cents=to_cents("1751.22"),
        quoted_otd_cents=to_cents("31306.22"),
        lines=[
            {"kind": "ADD_ON", "name": "VIN Etching", "price_cents": to_cents("269.00")},
            {"kind": "ADD_ON", "name": "Wheel Locks", "price_cents": to_cents("129.00")},
        ],
    )
    second = make_offer(
        db, dealer, days=2,
        msrp_cents=to_cents("29090.00"),
        selling_price_cents=to_cents("27329.42"),
        doc_fee_cents=to_cents("699.00"),
        other_non_tax_fees_cents=to_cents("368.00"),
        tax_cents=to_cents("1705.59"),
        quoted_otd_cents=to_cents("30500.01"),
        lines=[
            {"kind": "ADD_ON", "name": "VIN Etching", "price_cents": to_cents("269.00")},
            {"kind": "ADD_ON", "name": "Wheel Locks", "price_cents": to_cents("129.00")},
        ],
    )
    deltas = pricing.progression([pricing.compute(first), pricing.compute(second)])
    assert len(deltas) == 1
    delta = deltas[0]
    assert delta.otd_delta_cents == -to_cents("806.21")
    assert delta.selling_price_delta_cents == -to_cents("760.58")
    assert delta.improved is True
    assert delta.added_line_names == ()


def test_progression_detects_an_add_on_appearing(db):
    dealer = make_dealer(db, "Creep Honda")
    a = make_offer(db, dealer, days=0, selling_price_cents=to_cents("28000.00"),
                   tax_cents=to_cents("1680.00"))
    b = make_offer(
        db, dealer, days=1, selling_price_cents=to_cents("28000.00"),
        tax_cents=to_cents("1680.00"),
        lines=[{"kind": "ADD_ON", "name": "Nitrogen", "price_cents": to_cents("199.00")}],
    )
    delta = pricing.diff(pricing.compute(a), pricing.compute(b))
    assert delta.added_line_names == ("Nitrogen",)
    assert delta.add_ons_delta_cents == to_cents("199.00")


def test_money_formatting_is_exact_at_cent_boundaries():
    assert fmt(to_cents("30500.01")) == "$30,500.01"
    assert fmt(to_cents("0.005")) == "$0.01"  # half-up, not banker's
    assert fmt(-to_cents("806.21")) == "-$806.21"
    assert fmt(None) == "—"


def test_cents_conversion_never_loses_a_penny():
    for value in ("27329.42", "1705.59", "0.01", "99999.99"):
        assert fmt(to_cents(value)) == "$" + f"{float(value):,.2f}"


def test_an_offer_with_no_fee_lines_is_flagged_as_a_floor_not_a_figure(db):
    """Absence of a doc fee is not evidence that there isn't one."""
    dealer = make_dealer(db, "Ocean Honda Milford")
    offer = make_offer(db, dealer, selling_price_cents=to_cents("28820.00"))
    result = pricing.compute(offer)
    assert result.fees_disclosed is False
    assert any("floor, not a final number" in w for w in result.warnings)


def test_a_disclosed_doc_fee_clears_the_flag(db):
    dealer = make_dealer(db, "Honda of Westport")
    offer = make_offer(
        db, dealer, selling_price_cents=to_cents("28035.00"), doc_fee_cents=to_cents("699.00")
    )
    result = pricing.compute(offer)
    assert result.fees_disclosed is True
    assert not any("floor" in w for w in result.warnings)


def test_an_itemized_dealer_fee_line_also_clears_the_flag(db):
    dealer = make_dealer(db, "Itemized Honda")
    offer = make_offer(
        db,
        dealer,
        selling_price_cents=to_cents("28000.00"),
        lines=[{"kind": "DEALER_FEE", "name": "Doc prep", "price_cents": to_cents("199.00")}],
    )
    assert pricing.compute(offer).fees_disclosed is True
