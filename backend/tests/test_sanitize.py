"""Fixture sanitization.

Two properties matter and they pull against each other: nothing personal may survive,
and everything that makes the corpus testable must. These tests assert both sides.
"""

from __future__ import annotations

from datetime import datetime

from app.ingestion import eml
from app.ingestion.messages import RawMessage
from app.ingestion.sanitize import (
    FIXTURE_DOMAIN,
    Sanitizer,
    SanitizerConfig,
    sanitize_directory,
)

CONFIG = SanitizerConfig(
    buyer_addresses=("mike@personal.example",),
    buyer_names=("Mike Tester",),
)

QUOTE = """Hi Mike Tester,

Reach me at chris.benton@hondaofwestport.com or call (203) 555-9182.
We're at 184 Upper Independence Dr, Westport CT.

VIN 19XFL2H81TE040705, 16 miles.
Selling price       $28,035.00
Dealer fee          $699.00
PA sales tax        $1,688.10
Out the door        $30,775.10"""


def test_prices_and_wording_survive_untouched():
    """The whole point of the fixture is the negotiation, and the negotiation is money."""
    out = Sanitizer(CONFIG).scrub_text(QUOTE)
    for figure in ("$28,035.00", "$699.00", "$1,688.10", "$30,775.10"):
        assert figure in out
    for label in ("Selling price", "Dealer fee", "PA sales tax", "Out the door"):
        assert label in out
    assert "16 miles" in out


def test_personal_details_do_not_survive():
    out = Sanitizer(CONFIG).scrub_text(QUOTE)
    assert "Mike Tester" not in out
    assert "chris.benton@hondaofwestport.com" not in out
    assert "(203) 555-9182" not in out
    assert "184 Upper Independence Dr" not in out
    assert "19XFL2H81TE040705" not in out


def test_replacements_land_in_reserved_test_space():
    out = Sanitizer(CONFIG).scrub_text(QUOTE)
    assert FIXTURE_DOMAIN in out
    assert "(555) 555-01" in out  # 555-01xx is reserved and cannot be dialled


def test_the_buyer_gets_a_stable_recognisable_address():
    sanitizer = Sanitizer(CONFIG)
    assert sanitizer.fake_email("mike@personal.example") == f"buyer@{FIXTURE_DOMAIN}"


def test_the_same_real_value_always_becomes_the_same_fake_value():
    """Threading depends on this: one salesperson must not become four strangers."""
    a, b = Sanitizer(CONFIG), Sanitizer(CONFIG)
    assert a.fake_email("chris@westport.com") == b.fake_email("chris@westport.com")
    assert a.fake_person("Chris Benton") == b.fake_person("Chris Benton")


def test_different_people_get_different_identities():
    sanitizer = Sanitizer(CONFIG)
    assert sanitizer.fake_email("chris@westport.com") != sanitizer.fake_email(
        "edwin@stamford.com"
    )


def test_a_dealership_domain_maps_as_a_unit_so_resolution_still_works():
    sanitizer = Sanitizer(CONFIG)
    one = sanitizer.fake_email("chris@hondaofwestport.com")
    two = sanitizer.fake_email("edwin@hondaofwestport.com")
    assert one.split("@")[1] == two.split("@")[1]
    assert one != two


def test_vins_can_be_kept_when_they_are_wanted():
    sanitizer = Sanitizer(SanitizerConfig(keep_vins=True))
    assert "19XFL2H81TE040705" in sanitizer.scrub_text("VIN 19XFL2H81TE040705")


def test_arbitrary_literals_can_be_redacted():
    sanitizer = Sanitizer(SanitizerConfig(extra_literals=("Operation Bluebird",)))
    out = sanitizer.scrub_text("Re: Operation Bluebird pricing")
    assert "Bluebird" not in out
    assert "[REDACTED]" in out


def test_threading_headers_are_rewritten_consistently():
    sanitizer = Sanitizer(CONFIG)
    message = RawMessage(
        source_system="corpus",
        source_identifier="<abc@hondaofwestport.com>",
        sent_at=datetime(2026, 8, 31, 11, 24),
        thread_identifier="<thread-1@hondaofwestport.com>",
        rfc822_message_id="<abc@hondaofwestport.com>",
        in_reply_to="<thread-1@hondaofwestport.com>",
        references=("<thread-1@hondaofwestport.com>",),
        from_address="chris.benton@hondaofwestport.com",
        from_name="Chris Benton",
        to_addresses=("mike@personal.example",),
        subject="Re: pricing",
        body_text=QUOTE,
    )
    clean = sanitizer.scrub_message(message)

    assert "hondaofwestport.com>" not in clean.rfc822_message_id
    assert clean.in_reply_to == clean.references[0]
    assert clean.thread_identifier == clean.in_reply_to
    assert clean.to_addresses == (f"buyer@{FIXTURE_DOMAIN}",)
    assert clean.sent_at == message.sent_at  # chronology is part of the fixture


def test_attachments_are_dropped_rather_than_copied():
    """A PDF worksheet cannot be pattern-scrubbed, so it does not travel."""
    from app.ingestion.messages import Attachment

    message = RawMessage(
        source_system="corpus",
        source_identifier="m",
        sent_at=datetime(2026, 8, 31, 11, 24),
        attachments=(Attachment("buyers-order.pdf", "application/pdf", 1024, b"%PDF"),),
    )
    assert Sanitizer(CONFIG).scrub_message(message).attachments == ()


def test_residue_is_reported_rather_than_silently_accepted():
    sanitizer = Sanitizer(SanitizerConfig())
    message = RawMessage(
        source_system="corpus",
        source_identifier="m",
        sent_at=datetime(2026, 8, 31, 11, 24),
        body_text="Leftover: someone@stillreal.com",
    )
    assert sanitizer.check_residue(message)


def test_a_directory_round_trips_through_real_eml_files(tmp_path):
    source = tmp_path / "real"
    destination = tmp_path / "clean"
    source.mkdir()

    message = RawMessage(
        source_system="corpus",
        source_identifier="<m1@hondaofwestport.com>",
        sent_at=datetime(2026, 8, 31, 11, 24),
        rfc822_message_id="<m1@hondaofwestport.com>",
        from_address="chris.benton@hondaofwestport.com",
        from_name="Chris Benton",
        to_addresses=("mike@personal.example",),
        subject="Re: 2026 Civic pricing",
        body_text=QUOTE,
    )
    (source / "one.eml").write_bytes(eml.to_bytes(message))

    report = sanitize_directory(source, destination, config=CONFIG)

    assert len(report.files_written) == 1
    assert report.residue == []
    assert not (destination / "mapping.private.json").exists()

    reloaded = eml.load_directory(destination)[0]
    assert "$30,775.10" in reloaded.body_text
    assert "Mike Tester" not in reloaded.body_text
    assert reloaded.from_address.endswith(FIXTURE_DOMAIN)


def test_the_reidentification_key_is_only_written_when_asked(tmp_path):
    source, destination = tmp_path / "real", tmp_path / "clean"
    source.mkdir()
    (source / "one.eml").write_bytes(
        eml.to_bytes(
            RawMessage(
                source_system="corpus",
                source_identifier="<m@x.com>",
                sent_at=datetime(2026, 8, 31, 11, 24),
                from_address="chris@x.com",
                body_text="hello",
            )
        )
    )
    sanitize_directory(source, destination, config=CONFIG, write_mapping=True)
    assert (destination / "mapping.private.json").exists()
