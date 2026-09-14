"""Turning real correspondence into committable fixtures.

The rule this enforces: everything that makes the negotiation *testable* survives, and
everything that makes it *personal* does not. So prices, fees, add-on names, wording,
timestamps, ordering and thread relationships pass through untouched, while names,
addresses, phone numbers, street addresses and VINs are replaced.

Replacement is deterministic — the same real value always becomes the same fake value,
across every message in the corpus. That is what keeps threading intact: if
``chris.benton@westport.example`` became a different address in each file, dealer
resolution would see four strangers instead of one salesperson.

Nothing here is a substitute for reading the output. The tool reports what it replaced
and what it could not classify; the residue is for a human to look at before committing.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from app.ingestion import eml
from app.ingestion.messages import RawMessage, address_of

FIXTURE_DOMAIN = "example.test"

# Stable pools. Index chosen by hash of the real value, so the mapping is reproducible
# across machines and runs without storing a secret.
_FIRST_NAMES = (
    "Alex", "Blair", "Casey", "Drew", "Ellis", "Finley", "Gray", "Harper",
    "Indigo", "Jordan", "Kai", "Logan", "Morgan", "Noel", "Quinn", "Reese",
    "Sage", "Tatum", "Vale", "Wren",
)
_LAST_NAMES = (
    "Archer", "Bishop", "Calder", "Dunne", "Ellery", "Frost", "Granger", "Hale",
    "Irving", "Jarrett", "Keating", "Lowell", "Mercer", "Norris", "Osgood",
    "Prentice", "Quill", "Rivers", "Sutton", "Thorne",
)
_STREETS = (
    "Test Street", "Sample Avenue", "Fixture Lane", "Example Road", "Placeholder Way",
)


def _pick(pool: tuple[str, ...], value: str, salt: str = "") -> str:
    digest = hashlib.sha256(f"{salt}:{value.lower()}".encode()).digest()
    return pool[int.from_bytes(digest[:4], "big") % len(pool)]


# ------------------------------------------------------------------- patterns

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_PHONE = re.compile(
    r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"
)
_VIN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
_STREET_ADDRESS = re.compile(
    r"\b\d{1,5}\s+(?:[A-Z][\w.'-]+\s+){0,3}"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Boulevard|Blvd|Way|Court|Ct|"
    r"Turnpike|Pike|Highway|Hwy|Place|Pl|Terrace|Ter)\b\.?",
    re.IGNORECASE,
)
# Kept deliberately narrow: a false positive here silently destroys a price.
_CARD = re.compile(r"\b(?:\d[ -]?){13,16}\b")


@dataclass
class SanitizerConfig:
    """What to protect, and what to leave alone.

    ``keep_dealer_names`` defaults to True because dealership names are business
    identities, not personal data, and the golden assertions read much better with the
    real stores in them. Set it False if the corpus should be fully anonymous.
    """

    buyer_addresses: tuple[str, ...] = ()
    buyer_names: tuple[str, ...] = ()
    keep_dealer_names: bool = True
    keep_vins: bool = False
    extra_literals: tuple[str, ...] = ()


@dataclass
class SanitizerReport:
    mapping: dict[str, str] = field(default_factory=dict)
    counts: Counter = field(default_factory=Counter)
    files_written: list[str] = field(default_factory=list)
    residue: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{count} {kind}" for kind, count in sorted(self.counts.items())]
        return ", ".join(parts) or "nothing replaced"


class Sanitizer:
    """Deterministic, reversible-by-you-only pseudonymization."""

    def __init__(self, config: SanitizerConfig | None = None) -> None:
        self.config = config or SanitizerConfig()
        self.report = SanitizerReport()

    # ------------------------------------------------------------ primitives
    def _remember(self, kind: str, real: str, fake: str) -> str:
        if real not in self.report.mapping:
            self.report.mapping[real] = fake
            self.report.counts[kind] += 1
        return self.report.mapping[real]

    def fake_email(self, real: str) -> str:
        real = real.strip().lower()
        if real in self.report.mapping:
            return self.report.mapping[real]
        local, _, domain = real.partition("@")
        if real in {a.lower() for a in self.config.buyer_addresses}:
            return self._remember("email", real, f"buyer@{FIXTURE_DOMAIN}")
        # The domain carries the dealership's identity, which resolution relies on, so
        # it is mapped as a unit rather than per-address.
        fake_domain = self.fake_domain(domain)
        first = _pick(_FIRST_NAMES, local, "local").lower()
        last = _pick(_LAST_NAMES, local, "local").lower()
        return self._remember("email", real, f"{first}.{last}@{fake_domain}")

    def fake_domain(self, real: str) -> str:
        real = real.strip().lower()
        key = f"domain:{real}"
        if key in self.report.mapping:
            return self.report.mapping[key]
        label = re.sub(r"[^a-z0-9]+", "", real.split(".")[0]) or "dealer"
        fake = f"{label}.{FIXTURE_DOMAIN}"
        self.report.mapping[key] = fake
        self.report.counts["domain"] += 1
        return fake

    def fake_person(self, real: str) -> str:
        real = real.strip()
        if not real:
            return real
        if real in self.report.mapping:
            return self.report.mapping[real]
        fake = f"{_pick(_FIRST_NAMES, real)} {_pick(_LAST_NAMES, real, 'last')}"
        return self._remember("name", real, fake)

    def fake_phone(self, real: str) -> str:
        digits = re.sub(r"\D", "", real)
        # 555-01xx is reserved for fiction, so a fixture can never dial a real number.
        suffix = int(hashlib.sha256(digits.encode()).hexdigest()[:4], 16) % 100
        return self._remember("phone", real, f"(555) 555-01{suffix:02d}")

    def fake_vin(self, real: str) -> str:
        if self.config.keep_vins:
            return real
        digest = hashlib.sha256(real.encode()).hexdigest().upper()
        body = re.sub(r"[IOQ]", "X", digest)[:14]
        return self._remember("vin", real, f"VIN{body}")

    def fake_street(self, real: str) -> str:
        number = int(hashlib.sha256(real.encode()).hexdigest()[:3], 16) % 900 + 100
        return self._remember("address", real, f"{number} {_pick(_STREETS, real)}")

    # ------------------------------------------------------------------ text
    def scrub_text(self, text: str | None) -> str | None:
        if not text:
            return text
        out = text

        for literal in self.config.extra_literals:
            if literal and literal in out:
                out = out.replace(literal, self._remember("literal", literal, "[REDACTED]"))

        for name in self.config.buyer_names:
            if not name:
                continue
            out = re.sub(
                rf"(?<!\w){re.escape(name)}(?!\w)",
                self._remember("buyer name", name, "Test Buyer"),
                out,
                flags=re.IGNORECASE,
            )

        out = _CARD.sub(lambda m: self._remember("card", m.group(0), "[REDACTED]"), out)
        out = _STREET_ADDRESS.sub(lambda m: self.fake_street(m.group(0)), out)
        out = _EMAIL.sub(lambda m: self.fake_email(m.group(0)), out)
        out = _PHONE.sub(lambda m: self.fake_phone(m.group(0)), out)
        out = _VIN.sub(lambda m: self.fake_vin(m.group(0)), out)
        return out

    def _scrub_message_id(self, value: str | None) -> str | None:
        """Rewrite the domain inside <id@domain> so threading survives consistently."""
        if not value:
            return value
        return re.sub(
            r"@([\w.-]+)>",
            lambda m: f"@{self.fake_domain(m.group(1))}>",
            value,
        )

    # --------------------------------------------------------------- message
    def scrub_message(self, message: RawMessage) -> RawMessage:
        from dataclasses import replace

        return replace(
            message,
            source_identifier=self._scrub_message_id(message.source_identifier)
            or message.source_identifier,
            thread_identifier=self._scrub_message_id(message.thread_identifier),
            rfc822_message_id=self._scrub_message_id(message.rfc822_message_id),
            in_reply_to=self._scrub_message_id(message.in_reply_to),
            references=tuple(
                self._scrub_message_id(ref) or ref for ref in message.references
            ),
            from_address=self.fake_email(message.from_address) if message.from_address else None,
            from_name=self.fake_person(message.from_name) if message.from_name else None,
            to_addresses=tuple(self.fake_email(a) for a in message.to_addresses),
            cc_addresses=tuple(self.fake_email(a) for a in message.cc_addresses),
            subject=self.scrub_text(message.subject),
            body_text=self.scrub_text(message.body_text),
            body_html=self.scrub_text(message.body_html),
            snippet=self.scrub_text(message.snippet),
            attachments=(),
            raw_bytes=None,
        )

    # ------------------------------------------------------------- residue
    def check_residue(self, message: RawMessage) -> list[str]:
        """Anything that still looks personal after scrubbing.

        Reported rather than removed: a surprise here means the corpus contains a shape
        the patterns do not cover, and that is a human's call, not a regex's.
        """
        found: list[str] = []
        blob = " ".join(
            part
            for part in (message.subject, message.body_text, message.body_html)
            if part
        )
        for address in _EMAIL.findall(blob):
            if not address.endswith(FIXTURE_DOMAIN):
                found.append(f"address not rewritten: {address}")
        for phone in _PHONE.findall(blob):
            if "555" not in phone:
                found.append(f"phone not rewritten: {phone}")
        for vin in _VIN.findall(blob):
            if not self.config.keep_vins and not vin.startswith("VIN"):
                found.append(f"VIN not rewritten: {vin}")
        return found


def sanitize_directory(
    source: Path,
    destination: Path,
    *,
    config: SanitizerConfig | None = None,
    write_mapping: bool = False,
) -> SanitizerReport:
    """Sanitize every .eml under ``source`` into ``destination``.

    The mapping file is *not* written by default. It is a re-identification key: with
    it, the sanitized fixtures stop being sanitized. Keep it out of the repository.
    """
    sanitizer = Sanitizer(config)
    destination.mkdir(parents=True, exist_ok=True)

    for index, message in enumerate(eml.load_directory(source, source_system="corpus")):
        clean = sanitizer.scrub_message(message)
        sanitizer.report.residue.extend(
            f"{message.source_identifier}: {item}" for item in sanitizer.check_residue(clean)
        )
        # Named by position in the negotiation, which is how the fixtures are read.
        stem = f"{index:02d}-{_slug(clean.subject)}"
        path = destination / f"{stem}.eml"
        path.write_bytes(eml.to_bytes(clean))
        sanitizer.report.files_written.append(path.name)

    if write_mapping:
        (destination / "mapping.private.json").write_text(
            json.dumps(sanitizer.report.mapping, indent=2, sort_keys=True)
        )
    return sanitizer.report


def _slug(value: str | None) -> str:
    text = re.sub(r"^(re|fwd?):\s*", "", (value or "message").strip(), flags=re.IGNORECASE)
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text or "message")[:48]


def config_from_profile(profile, extra_literals: tuple[str, ...] = ()) -> SanitizerConfig:
    """Build a config from the buyer profile, which already knows who the buyer is."""
    addresses = tuple(a for a in [address_of(getattr(profile, "email", None))] if a)
    names = tuple(n for n in [getattr(profile, "display_name", None)] if n)
    return SanitizerConfig(
        buyer_addresses=addresses,
        buyer_names=names,
        extra_literals=tuple(
            literal
            for literal in (*extra_literals, getattr(profile, "phone", None) or "")
            if literal
        ),
    )
