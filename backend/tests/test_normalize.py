"""Body normalization: HTML flattening, quoted replies, signatures."""

from __future__ import annotations

from app.ingestion.normalize import html_to_text, normalize, split_body


def test_html_tables_keep_their_columns_apart():
    """Dealer quotes arrive as two-column tables, and a run-together cell is unparseable."""
    html = """<table>
      <tr><td>Selling price</td><td>$28,035.00</td></tr>
      <tr><td>Dealer fee</td><td>$699.00</td></tr>
    </table>"""
    text = html_to_text(html)
    assert "Selling price  $28,035.00" in text
    assert "Dealer fee  $699.00" in text


def test_html_scripts_and_styles_are_dropped():
    html = "<style>.a{color:red}</style><p>Hello</p><script>alert(1)</script>"
    assert html_to_text(html) == "Hello"


def test_html_entities_are_decoded():
    assert "Selling price — $28,035" in html_to_text("<p>Selling price &mdash; $28,035</p>")


def test_a_quoted_reply_chain_is_split_off_not_deleted():
    body = """Thanks, that works for me.

On Tue, Sep 2, 2026 at 4:03 PM Edwin Sanchez <edwin@example.test> wrote:
> Selling price $28,090.00
> Out the door $31,306.22"""
    result = split_body(body)
    assert result.text == "Thanks, that works for me."
    assert "$31,306.22" in result.quoted


def test_the_buyers_own_words_cannot_be_read_back_as_the_dealers_position():
    """The failure this guards against: a quoted price treated as the dealer agreeing."""
    body = """I can't do that.

-----Original Message-----
From: buyer@example.test
I'll do $27,750 with no add-ons."""
    result = split_body(body)
    assert "$27,750" not in result.text
    assert "$27,750" in result.quoted


def test_a_signature_block_is_separated():
    body = "Here are the numbers.\n\n--\nChris Benton\nInternet Sales\n(555) 555-0101"
    result = split_body(body)
    assert result.text == "Here are the numbers."
    assert "Chris Benton" in result.signature


def test_a_bare_quote_block_with_no_header_is_still_detected():
    body = "Sure.\n\n> Can you send the OTD?\n> Thanks\n> -- buyer"
    result = split_body(body)
    assert result.text == "Sure."
    assert "Can you send the OTD?" in result.quoted


def test_stripping_is_conservative_about_ordinary_prose():
    body = "On the phone you mentioned no add-ons. Can you confirm in writing?"
    result = split_body(body)
    assert result.text == body
    assert result.quoted == ""


def test_plain_text_wins_over_html_when_both_are_present():
    result = normalize("Plain version", "<p>HTML version</p>")
    assert result.text == "Plain version"


def test_html_is_used_when_there_is_no_plain_part():
    result = normalize(None, "<p>HTML only</p>")
    assert result.text == "HTML only"


def test_an_empty_body_is_not_an_error():
    result = normalize(None, None)
    assert result.text == ""
    assert result.has_new_content is False
