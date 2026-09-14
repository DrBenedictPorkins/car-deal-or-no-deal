"""Rule-based extraction of offers from dealer correspondence.

Deterministic, offline, and auditable. Two reasons it exists rather than going straight
to an LLM:

* Arithmetic stays in code. This module reads *labelled amounts* and nothing else — it
  never adds a column of figures up, never infers a selling price by subtracting a fee,
  and never reconciles a total. Those are ``pricing.py``'s job, on the structured
  result.
* It is the baseline the LLM extractor has to match. When Phase 3 lands, the golden
  fixtures assert the same expected values against both paths; a model that disagrees
  with the parser on a figure it can see is a model that is guessing.

Every extracted amount carries the verbatim span it came from, with offsets, so each
resulting fact is clickable back to the exact characters in the source email.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.enums import Confidence

# ------------------------------------------------------------------ vocabulary

# Longest phrases first so "adjusted selling price" wins over "selling price" and
# "sales tax" wins over "tax".
FIELD_SYNONYMS: tuple[tuple[str, str], ...] = (
    ("adjusted selling price", "selling_price_cents"),
    ("negotiated selling price", "selling_price_cents"),
    ("discounted selling price", "selling_price_cents"),
    ("final selling price", "selling_price_cents"),
    ("selling price", "selling_price_cents"),
    ("sale price", "selling_price_cents"),
    ("sales price", "selling_price_cents"),
    ("vehicle price", "selling_price_cents"),
    ("price of the vehicle", "selling_price_cents"),
    ("advertised price", "advertised_price_cents"),
    ("internet price", "advertised_price_cents"),
    ("online price", "advertised_price_cents"),
    ("e-price", "advertised_price_cents"),
    ("msrp", "msrp_cents"),
    ("sticker price", "msrp_cents"),
    ("destination charge", "destination_cents"),
    ("destination fee", "destination_cents"),
    ("destination", "destination_cents"),
    ("documentation fee", "doc_fee_cents"),
    ("documentary fee", "doc_fee_cents"),
    ("doc fee", "doc_fee_cents"),
    ("dealer fee", "doc_fee_cents"),
    ("dealer conveyance fee", "doc_fee_cents"),
    ("conveyance fee", "doc_fee_cents"),
    ("processing fee", "processing_fee_cents"),
    ("dealer processing", "processing_fee_cents"),
    ("registration fee", "registration_cents"),
    ("registration", "registration_cents"),
    ("reg fee", "registration_cents"),
    ("plate fee", "registration_cents"),
    ("plates", "registration_cents"),
    ("tags", "registration_cents"),
    ("dmv fee", "registration_cents"),
    ("title fee", "title_fee_cents"),
    ("title", "title_fee_cents"),
    ("sales tax", "tax_cents"),
    ("state tax", "tax_cents"),
    ("use tax", "tax_cents"),
    ("pa tax", "tax_cents"),
    ("ct tax", "tax_cents"),
    ("ny tax", "tax_cents"),
    ("tax", "tax_cents"),
    ("non-tax fees", "other_non_tax_fees_cents"),
    ("non tax fees", "other_non_tax_fees_cents"),
    ("nontaxable fees", "other_non_tax_fees_cents"),
    ("non-taxable fees", "other_non_tax_fees_cents"),
    ("taxable fees", "other_taxable_fees_cents"),
    ("out the door", "quoted_otd_cents"),
    ("out-the-door", "quoted_otd_cents"),
    ("otd", "quoted_otd_cents"),
    ("drive out price", "quoted_otd_cents"),
    ("total due", "quoted_otd_cents"),
    ("total price", "quoted_otd_cents"),
    ("balance due", "quoted_otd_cents"),
    ("dealer discount", "discount_cents"),
    ("discount", "discount_cents"),
    ("rebate", "discount_cents"),
    ("savings", "discount_cents"),
    ("off", "discount_cents"),
)

# Compiled once, word-bounded. Substring matching would let "tax" claim the figure
# next to "taxable fees" and "off" claim the one next to "offer".
_SYNONYM_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = tuple(
    (re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)", re.IGNORECASE), phrase, target)
    for phrase, target in FIELD_SYNONYMS
)

# Named accessories and packages. A labelled amount matching one of these becomes an
# itemized add-on line rather than an unrecognized figure.
ADD_ON_HINTS: tuple[str, ...] = (
    "vin etch",
    "etching",
    "etch",
    "wheel lock",
    "nitrogen",
    "ceramic coating",
    "paint protection",
    "paint sealant",
    "fabric protection",
    "interior protection",
    "pinstripe",
    "splash guard",
    "mud guard",
    "mudguard",
    "window tint",
    "tint",
    "clear bra",
    "door edge guard",
    "lojack",
    "anti-theft",
    "theft protection",
    "gap insurance",
    "appearance package",
    "protection package",
    "maintenance plan",
    "maintenance package",
    "service contract",
    "extended warranty",
    "key replacement",
    "tire and wheel",
    "roadside",
)

_MONEY = re.compile(
    r"""
    (?P<dollar>\$)?\s?
    (?P<amount>
        \d{1,3}(?:,\d{3})+(?:\.\d{2})?   # 28,035.00 / 28,035
      | \d+\.\d{2}                        # 699.00
      | \d{3,6}                           # 699 / 27954
    )
    """,
    re.VERBOSE,
)

# Anything within this many characters of an amount can label it.
_LABEL_WINDOW_BEFORE = 80
_LABEL_WINDOW_AFTER = 45
_MAX_LABEL_DISTANCE = 42


@dataclass(frozen=True)
class Evidence:
    """The verbatim span behind one extracted value."""

    target: str
    label: str
    amount_cents: int
    quote: str
    start: int
    end: int


@dataclass
class AddOnCandidate:
    name: str
    price_cents: int
    quote: str
    start: int
    end: int
    mandatory_claimed: bool | None = None
    already_installed: bool | None = None


@dataclass
class OfferCandidate:
    """Structured reading of one message. Contains no computed totals, by design."""

    fields: dict[str, int] = field(default_factory=dict)
    add_ons: list[AddOnCandidate] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)

    financing_required: bool | None = None
    financing_provider: str | None = None
    apr_bp: int | None = None
    financing_term_months: int | None = None
    minimum_loan_months: int | None = None
    prepayment_penalty: bool | None = None
    discount_clawback: bool | None = None
    advertised_includes_fees: bool | None = None

    deadline_note: str | None = None
    vin: str | None = None
    mileage: int | None = None
    is_demo: bool | None = None
    is_loaner: bool | None = None

    conflicts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def has_pricing(self) -> bool:
        priced = {"selling_price_cents", "advertised_price_cents", "quoted_otd_cents"}
        return bool(priced & set(self.fields)) or bool(self.add_ons)

    @property
    def confidence(self) -> str:
        """How much of a complete quote this message actually contained."""
        if not self.has_pricing:
            return Confidence.LOW
        anchors = {"selling_price_cents", "advertised_price_cents"}
        has_anchor = bool(anchors & set(self.fields))
        has_total = "quoted_otd_cents" in self.fields
        has_tax = "tax_cents" in self.fields
        if has_anchor and has_total and has_tax:
            return Confidence.HIGH
        if has_anchor or has_total:
            return Confidence.MEDIUM
        return Confidence.LOW


# ---------------------------------------------------------------- amount parsing


def parse_amount(text: str) -> int | None:
    """"28,035.00" → 2803500. Integer cents; no floats in the money path."""
    cleaned = text.replace(",", "").strip()
    if not cleaned:
        return None
    if "." in cleaned:
        whole, _, frac = cleaned.partition(".")
        frac = (frac + "00")[:2]
    else:
        whole, frac = cleaned, "00"
    if not whole.isdigit():
        return None
    return int(whole) * 100 + int(frac)


def _line_of(text: str, position: int) -> tuple[str, int, int]:
    start = text.rfind("\n", 0, position) + 1
    end = text.find("\n", position)
    end = len(text) if end == -1 else end
    return text[start:end].strip(), start, end


def _nearest_label(before: str, after: str) -> tuple[str | None, str | None, int]:
    """Closest field synonym to an amount, searching backwards then forwards."""
    best: tuple[str | None, str | None, int] = (None, None, 10**6)

    for pattern, phrase, target in _SYNONYM_PATTERNS:
        hits = list(pattern.finditer(before))
        if hits:
            distance = len(before) - hits[-1].end()
            # A longer phrase at the same distance is the more specific reading.
            if distance < best[2] or (distance == best[2] and len(phrase) > len(best[1] or "")):
                best = (target, phrase, distance)
        hit = pattern.search(after)
        if hit and (
            hit.start() < best[2]
            or (hit.start() == best[2] and len(phrase) > len(best[1] or ""))
        ):
            best = (target, phrase, hit.start())
    return best


def _add_on_label(before: str, after: str) -> str | None:
    """The accessory name nearest an amount, if any."""
    lowered_before = before.lower()
    spans = [
        (index, index + len(hint))
        for index, hint in ((lowered_before.rfind(hint), hint) for hint in ADD_ON_HINTS)
        if index != -1
    ]
    if spans:
        # The hint list overlaps itself — "vin etch", "etch" and "etching" all fire on
        # "VIN etching". Merging the overlapping spans recovers the whole name instead
        # of whichever fragment happened to reach furthest right.
        spans.sort()
        start, end = spans[-1]
        for other_start, other_end in reversed(spans[:-1]):
            if other_end >= start:
                start = min(start, other_start)
                end = max(end, other_end)
        tail = before[start:].strip(" .:-\t")
        return tail or before[start:end]
    lowered_after = after.lower()
    for hint in ADD_ON_HINTS:
        if hint in lowered_after:
            index = lowered_after.index(hint)
            return after[index : index + len(hint)].strip()
    return None


@dataclass(frozen=True)
class _Claim:
    """One amount's bid to be a particular field, with how near its label sat."""

    target: str
    label: str
    amount_cents: int
    distance: int
    quote: str
    start: int
    end: int


def _assign(candidate: OfferCandidate, claims: list[_Claim]) -> None:
    """Resolve competing claims by proximity rather than by reading order.

    "got approval for $500 off, so $28,820 selling price" produces two bids for the
    selling price. Taking whichever came first in the text would record $500 as the
    price of a car; the label sitting one character from $28,820 is the honest reading.
    """
    by_target: dict[str, list[_Claim]] = {}
    for claim in claims:
        by_target.setdefault(claim.target, []).append(claim)

    for target, group in by_target.items():
        group.sort(key=lambda c: (c.distance, c.start))
        winner = group[0]
        candidate.fields[target] = winner.amount_cents
        candidate.evidence.append(
            Evidence(
                target=winner.target,
                label=winner.label,
                amount_cents=winner.amount_cents,
                quote=winner.quote,
                start=winner.start,
                end=winner.end,
            )
        )
        for loser in group[1:]:
            if loser.amount_cents == winner.amount_cents:
                continue
            candidate.conflicts.append(
                f"{target.removesuffix('_cents').replace('_', ' ')} was claimed by two "
                f"amounts ({winner.amount_cents / 100:,.2f} and "
                f"{loser.amount_cents / 100:,.2f}); kept the one nearer its label."
            )


def _label_from_previous_line(text: str, line_start: int) -> tuple[str | None, str | None, int]:
    """Handle quotes laid out as a label line followed by an amount line."""
    if line_start == 0:
        return None, None, 10**6
    previous, _, _ = _line_of(text, line_start - 1)
    if not previous or any(character.isdigit() for character in previous):
        return None, None, 10**6
    target, label, _ = _nearest_label(previous, "")
    return (target, label, 0) if target else (None, None, 10**6)


def _looks_like_a_year(match: re.Match[str]) -> bool:
    amount = match.group("amount")
    return (
        not match.group("dollar")
        and amount.isdigit()
        and len(amount) == 4
        and amount.startswith(("19", "20"))
    )


# ------------------------------------------------------------------- financing

_FINANCING_REQUIRED = re.compile(
    r"(?:requires?|must|only (?:if|with)|conditioned on|contingent on|need(?:s)? to)"
    r"[^.\n]{0,40}\bfinanc\w+",
    re.IGNORECASE,
)
_FINANCING_PROVIDER = re.compile(
    r"financ\w*\s+(?:through|with|via|by)\s+([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})"
)
_APR = re.compile(r"(\d{1,2}(?:\.\d{1,2})?)\s*%", re.IGNORECASE)
_TERM = re.compile(r"(\d{2,3})\s*(?:-|\s)?\s*month", re.IGNORECASE)
_NO_PREPAY = re.compile(
    r"\bno\b[^.\n]{0,30}\bprepayment penalt", re.IGNORECASE
)
_HAS_PREPAY = re.compile(
    r"\b(?:there is|there's|has)\b[^.\n]{0,20}\ba prepayment penalt", re.IGNORECASE
)
_MIN_TERM = re.compile(
    r"(?:minimum|at least|keep (?:it|the loan) (?:for|open))[^.\n]{0,25}?(\d{1,3})\s*"
    r"(?:month|payment)",
    re.IGNORECASE,
)
_CLAWBACK = re.compile(
    r"(?:claw\s?back|charge ?back|lose the (?:discount|rebate)|discount (?:is )?forfeit)",
    re.IGNORECASE,
)
_DEADLINE = re.compile(
    r"(?:by\s+(?:tonight|today|tomorrow|end of (?:day|business)|close of business|"
    r"this (?:evening|afternoon|week))"
    r"|expires?\s+[^.\n]{0,30}"
    r"|good (?:through|until|till)\s+[^.\n]{0,30}"
    r"|valid (?:through|until)\s+[^.\n]{0,30}"
    r"|(?:today|tonight) only)",
    re.IGNORECASE,
)

_VIN = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b")
_MILEAGE = re.compile(r"\b([\d,]{1,7})\s*(?:miles|mi)\b", re.IGNORECASE)
_NOT_DEMO = re.compile(r"\bnot?\s+a\s+demo\b|\bnever\s+(?:a\s+)?demo\b", re.IGNORECASE)
_IS_DEMO = re.compile(r"\b(?:it(?:'s| is)|this is)\s+(?:a\s+)?demo\b", re.IGNORECASE)
_NOT_LOANER = re.compile(r"\bnot?\s+a\s+loaner\b", re.IGNORECASE)
_MANDATORY = re.compile(
    r"\b(?:mandatory|required|cannot be removed|can't be removed|non-?negotiable|"
    r"already (?:on|installed))",
    re.IGNORECASE,
)
_INSTALLED = re.compile(r"\balready\s+(?:on the car|installed|applied)\b", re.IGNORECASE)


_CANNOT_REMOVE = re.compile(
    r"\b(?:can'?t|cannot|unable to)\s+(?:be\s+)?(?:strip|remove|take)\w*\b"
    r"|\bnon-?removable\b|\bthey (?:stay|have to stay)\b",
    re.IGNORECASE,
)


def _apply_document_claims(text: str, candidate: OfferCandidate) -> None:
    """Statements about the add-ons collectively, made away from the line items.

    "The etch and locks are already on the car so I can't strip them" sits three lines
    below the figures it describes, well outside any per-amount window, but it is the
    dealer's claim about every add-on in the quote.
    """
    if not candidate.add_ons:
        return
    installed = bool(_INSTALLED.search(text))
    immovable = bool(_CANNOT_REMOVE.search(text))
    mandatory = bool(_MANDATORY.search(text)) or immovable
    if not (installed or mandatory):
        return
    for add_on in candidate.add_ons:
        if installed and add_on.already_installed is None:
            add_on.already_installed = True
        if mandatory and add_on.mandatory_claimed is None:
            add_on.mandatory_claimed = True
    candidate.notes.append(
        "The dealer described the add-ons as "
        + " and ".join(
            part
            for part in (
                "already installed" if installed else "",
                "not removable" if immovable else ("mandatory" if mandatory else ""),
            )
            if part
        )
        + "."
    )


def _extract_financing(text: str, candidate: OfferCandidate) -> None:
    if _FINANCING_REQUIRED.search(text):
        candidate.financing_required = True
        provider = _FINANCING_PROVIDER.search(text)
        if provider:
            candidate.financing_provider = provider.group(1).strip(" .,")
    elif re.search(r"\bno financing (?:requirement|required)\b", text, re.IGNORECASE) or (
        re.search(r"\bthat'?s the cash price\b", text, re.IGNORECASE)
    ):
        candidate.financing_required = False

    apr = _APR.search(text)
    if apr:
        candidate.apr_bp = int(round(float(apr.group(1)) * 100))
    term = _TERM.search(text)
    if term:
        candidate.financing_term_months = int(term.group(1))
    minimum = _MIN_TERM.search(text)
    if minimum:
        candidate.minimum_loan_months = int(minimum.group(1))

    # Only recorded when the text is unambiguous. NULL means "nobody has said", and
    # that distinction is the whole basis of the finance-then-pay-off analysis.
    if _NO_PREPAY.search(text):
        candidate.prepayment_penalty = False
    elif _HAS_PREPAY.search(text):
        candidate.prepayment_penalty = True
    if _CLAWBACK.search(text):
        candidate.discount_clawback = True

    deadline = _DEADLINE.search(text)
    if deadline:
        candidate.deadline_note = deadline.group(0).strip(" .,")


def _extract_vehicle_facts(text: str, candidate: OfferCandidate) -> None:
    vin = _VIN.search(text)
    if vin:
        candidate.vin = vin.group(1)
    mileage = _MILEAGE.search(text)
    if mileage:
        try:
            candidate.mileage = int(mileage.group(1).replace(",", ""))
        except ValueError:
            pass
    if _NOT_DEMO.search(text):
        candidate.is_demo = False
    elif _IS_DEMO.search(text):
        candidate.is_demo = True
    if _NOT_LOANER.search(text):
        candidate.is_loaner = False


# --------------------------------------------------------------------- entry point


def extract(text: str) -> OfferCandidate:
    """Read one message. Returns labelled amounts and line items — never a total."""
    candidate = OfferCandidate()
    if not text or not text.strip():
        return candidate

    claims: list[_Claim] = []
    for match in _MONEY.finditer(text):
        if _looks_like_a_year(match):
            continue
        amount = parse_amount(match.group("amount"))
        if amount is None:
            continue

        line, line_start, line_end = _line_of(text, match.start())

        # Labels never cross a line boundary. In a quote laid out as a column, the
        # text following "$28,035.00" is the *next* line's label, and letting the
        # window reach it files the selling price under whatever comes next.
        before = text[max(line_start, match.start() - _LABEL_WINDOW_BEFORE) : match.start()]
        after = text[match.end() : min(line_end, match.end() + _LABEL_WINDOW_AFTER)]

        target, label, distance = _nearest_label(before, after)
        add_on_name = _add_on_label(before, after)

        # One concession to layout: a bare label on its own line above the figure.
        if target is None:
            target, label, distance = _label_from_previous_line(text, line_start)

        # An accessory named right next to the figure beats a generic field label
        # further away: "VIN etching $269" under a "Dealer fees" heading is an add-on.
        if add_on_name and (target is None or distance > 12):
            window = f"{before} {after}"
            candidate.add_ons.append(
                AddOnCandidate(
                    name=_title_case(add_on_name),
                    price_cents=amount,
                    quote=line,
                    start=line_start,
                    end=line_end,
                    mandatory_claimed=True if _MANDATORY.search(window) else None,
                    already_installed=True if _INSTALLED.search(window) else None,
                )
            )
            continue

        if target is None or distance > _MAX_LABEL_DISTANCE:
            continue

        claims.append(
            _Claim(
                target=target,
                label=label or target,
                amount_cents=amount,
                distance=distance,
                quote=line,
                start=line_start,
                end=line_end,
            )
        )

    _assign(candidate, claims)
    _apply_document_claims(text, candidate)
    _extract_financing(text, candidate)
    _extract_vehicle_facts(text, candidate)

    # "advertised price ... which includes our $175 doc fee" — the fee is inside the
    # figure, so adding it again would double-count. Recorded as a flag; the
    # arithmetic consequences are pricing.py's problem, not this module's.
    if "advertised_price_cents" in candidate.fields and re.search(
        r"\b(which |that )?includ\w+\b", text, re.IGNORECASE
    ):
        candidate.advertised_includes_fees = True
        candidate.notes.append(
            "The advertised price is described as already including fees."
        )

    return candidate


def _title_case(value: str) -> str:
    small = {"and", "the", "of", "a", "with"}
    words = re.split(r"\s+", value.strip())
    return " ".join(
        word if word.isupper() else (word.lower() if word.lower() in small else word.title())
        for word in words
        if word
    )
