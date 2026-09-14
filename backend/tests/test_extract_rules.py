"""Rule-based extraction.

The parser reads labelled amounts. It does not add anything up, and these tests are
mostly about the ways a label can be attached to the wrong number.
"""

from __future__ import annotations

import pytest

from app.enrichment.extract_rules import extract, parse_amount
from app.services.money import to_cents


def test_amounts_become_exact_cents():
    assert parse_amount("28,035.00") == 2803500
    assert parse_amount("699") == 69900
    assert parse_amount("1,688.10") == 168810
    assert parse_amount("0.01") == 1


def test_a_columnar_quote_is_read_line_by_line():
    """Each label belongs to the figure on its own line, not the one below it."""
    result = extract(
        """Selling price       $28,035.00
Dealer fee          $699.00
Registration        $253.00
PA sales tax        $1,688.10
Out the door        $30,775.10"""
    )
    assert result.fields == {
        "selling_price_cents": to_cents("28035.00"),
        "doc_fee_cents": to_cents("699.00"),
        "registration_cents": to_cents("253.00"),
        "tax_cents": to_cents("1688.10"),
        "quoted_otd_cents": to_cents("30775.10"),
    }


def test_the_nearest_label_wins_not_the_first_one_read():
    """"$500 off, so $28,820 selling price" — reading order would price the car at $500."""
    result = extract("I got approval for $500 off, so $28,820 selling price.")
    assert result.fields["selling_price_cents"] == to_cents("28820.00")
    assert result.fields["discount_cents"] == to_cents("500.00")


def test_a_label_on_the_line_above_its_figure_is_still_matched():
    result = extract("Out the door\n$30,500.01")
    assert result.fields["quoted_otd_cents"] == to_cents("30500.01")


def test_word_boundaries_stop_tax_claiming_the_taxable_fees_line():
    result = extract("Taxable fees   $150.00\nSales tax      $1,700.00")
    assert result.fields["other_taxable_fees_cents"] == to_cents("150.00")
    assert result.fields["tax_cents"] == to_cents("1700.00")


def test_adjusted_selling_price_beats_the_shorter_synonym():
    result = extract("Adjusted selling price  $27,329.42")
    assert result.fields["selling_price_cents"] == to_cents("27329.42")


def test_accessories_become_itemized_add_ons_with_their_full_names():
    result = extract("VIN etching   $269.00\nWheel locks   $129.00")
    assert [(a.name, a.price_cents) for a in result.add_ons] == [
        ("VIN Etching", to_cents("269.00")),
        ("Wheel Locks", to_cents("129.00")),
    ]
    assert "add_ons_total_cents" not in result.fields  # totals are not this module's job


def test_a_claim_about_the_add_ons_made_further_down_still_attaches():
    result = extract(
        "VIN etching  $269.00\nWheel locks  $129.00\n\n"
        "They're already on the car so I can't strip them."
    )
    assert all(a.already_installed and a.mandatory_claimed for a in result.add_ons)


def test_a_model_year_is_not_money():
    result = extract("We have a 2026 Civic Sport in stock. Selling price $28,000.00.")
    assert result.fields == {"selling_price_cents": to_cents("28000.00")}


def test_financing_conditions_are_extracted_from_prose():
    result = extract(
        "Our advertised price is $27,954 which includes our $175 doc fee. That price "
        "does require financing through Honda Financial — 7.14% for 84 months."
    )
    assert result.fields["advertised_price_cents"] == to_cents("27954.00")
    assert result.fields["doc_fee_cents"] == to_cents("175.00")
    assert result.advertised_includes_fees is True
    assert result.financing_required is True
    assert result.financing_provider == "Honda Financial"
    assert result.apr_bp == 714
    assert result.financing_term_months == 84


def test_unstated_financing_conditions_stay_null():
    """NULL means nobody has said, and that is the basis of the payoff analysis."""
    result = extract("Price requires financing through Honda Financial at 7.14%.")
    assert result.prepayment_penalty is None
    assert result.discount_clawback is None
    assert result.minimum_loan_months is None


def test_stated_financing_conditions_are_captured():
    result = extract(
        "There is no prepayment penalty, but you do have to keep the loan for 6 months "
        "or we claw back the discount."
    )
    assert result.prepayment_penalty is False
    assert result.discount_clawback is True
    assert result.minimum_loan_months == 6


def test_a_cash_price_is_recorded_as_not_requiring_financing():
    result = extract("Selling price $28,035.00. No financing requirement — that's the cash price.")
    assert result.financing_required is False


def test_vehicle_facts_come_out_of_the_same_pass():
    result = extract(
        "Meteorite Gray, VIN 19XFL2H81TE040705, 10 miles. Brand new, not a demo, "
        "not a loaner."
    )
    assert result.vin == "19XFL2H81TE040705"
    assert result.mileage == 10
    assert result.is_demo is False
    assert result.is_loaner is False


def test_a_deadline_is_noted_verbatim():
    result = extract("We can do $27,500 but I need to know by tonight.")
    assert result.deadline_note
    assert "tonight" in result.deadline_note.lower()


def test_a_message_with_no_pricing_produces_nothing():
    result = extract("Thanks for your interest! What's a good time to call?")
    assert result.has_pricing is False
    assert result.fields == {}
    assert result.confidence == "LOW"


@pytest.mark.parametrize("text", ["", "   ", "\n\n"])
def test_empty_input_is_handled(text):
    assert extract(text).has_pricing is False


def test_confidence_reflects_how_complete_the_quote_was():
    complete = extract("Selling price $28,000.00\nSales tax $1,680.00\nOut the door $29,680.00")
    partial = extract("Selling price $28,000.00")
    assert complete.confidence == "HIGH"
    assert partial.confidence == "MEDIUM"


def test_two_amounts_claiming_one_field_are_reported_as_a_conflict():
    result = extract("Selling price $28,000.00 but the selling price is $27,500.00")
    assert result.conflicts
    assert "selling price" in result.conflicts[0]


def test_every_figure_carries_the_line_it_came_from():
    result = extract("Selling price       $28,035.00\nDealer fee          $699.00")
    assert {e.target for e in result.evidence} == {"selling_price_cents", "doc_fee_cents"}
    for evidence in result.evidence:
        assert evidence.quote
        assert evidence.start < evidence.end
