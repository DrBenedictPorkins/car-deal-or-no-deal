"""Human or machine?

The brief singles this out, and rightly: in the source negotiation several messages
signed by a "Sales Manager" were sent by a lead-management system and restated the
buyer's own inquiry almost word for word. Treating those as genuine replies inflates a
dealer's responsiveness and — worse — lets extraction read the buyer's own terms back
as the dealer's position.

Rules first, and every verdict carries the sentence that justifies it. A classification
without a stated reason is not acceptable, so ``reason`` is not optional.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.enums import ActorKind
from app.ingestion.messages import RawMessage, address_of, domain_of

NO_REPLY_LOCALPARTS = (
    "no-reply",
    "noreply",
    "donotreply",
    "do-not-reply",
    "mailer-daemon",
    "notifications",
    "autoresponder",
)

# Lead-management and CRM platforms that send on a dealership's behalf. Matching is
# on the mail domain, which is what actually distinguishes them from a person at the
# store writing a reply.
LEAD_PLATFORM_HINTS = (
    "dealersocket",
    "vinsolutions",
    "elead",
    "eleadcrm",
    "dealer.com",
    "dealerinspire",
    "autobytel",
    "carsdirect",
    "cars.com",
    "cargurus",
    "truecar",
    "leadmanager",
    "leads",
    "crm",
    "marketing",
    "campaign",
)

_WORD = re.compile(r"[a-z0-9$.,%]+")


@dataclass(frozen=True)
class Classification:
    actor_kind: str
    reason: str
    confidence: float

    @property
    def is_automated(self) -> bool:
        return self.actor_kind == ActorKind.AUTOMATED


def _shingles(text: str, size: int = 6) -> set[str]:
    words = _WORD.findall(text.lower())
    if len(words) < size:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + size]) for i in range(len(words) - size + 1)}


def restatement_ratio(candidate: str, reference: str) -> float:
    """Fraction of the candidate that also appears in the reference text.

    Used to catch "I see you were looking for a new 2026 Honda Civic Hatchback Sport
    (non-hybrid), in a dark or subdued colour — no white…", which is the buyer's own
    inquiry played back.
    """
    candidate_shingles = _shingles(candidate)
    if not candidate_shingles:
        return 0.0
    reference_shingles = _shingles(reference)
    if not reference_shingles:
        return 0.0
    return len(candidate_shingles & reference_shingles) / len(candidate_shingles)


def classify(
    message: RawMessage,
    normalized_text: str,
    *,
    buyer_texts: list[str] | None = None,
    prior_dealer_texts: list[str] | None = None,
) -> Classification:
    """Classify one inbound message.

    ``buyer_texts`` are the buyer's own outbound messages and ``prior_dealer_texts``
    are earlier inbound messages from the same dealer — both are needed because the
    two strongest signals are "this is my own words back" and "this is the same
    message they already sent".
    """
    address = address_of(message.from_address) or ""
    local = address.split("@")[0] if "@" in address else address
    domain = domain_of(message.from_address) or ""

    if any(marker in local for marker in NO_REPLY_LOCALPARTS):
        return Classification(
            ActorKind.AUTOMATED,
            f"Sent from a no-reply address ({address}).",
            0.98,
        )

    hit = next((hint for hint in LEAD_PLATFORM_HINTS if hint in domain), None)
    if hit:
        return Classification(
            ActorKind.AUTOMATED,
            f"Sent through a lead-management domain ({domain}, matched '{hit}').",
            0.9,
        )

    if "precedence: bulk" in (message.mime_summary or "").lower():
        return Classification(ActorKind.AUTOMATED, "Marked as bulk mail.", 0.9)

    for buyer_text in buyer_texts or []:
        ratio = restatement_ratio(normalized_text, buyer_text)
        if ratio >= 0.35:
            return Classification(
                ActorKind.AUTOMATED,
                (
                    f"{ratio:.0%} of this message repeats the buyer's own inquiry "
                    f"verbatim — the signature of a lead-management auto-responder "
                    f"rather than a written reply."
                ),
                0.85,
            )

    for prior in prior_dealer_texts or []:
        ratio = restatement_ratio(normalized_text, prior)
        if ratio >= 0.8:
            return Classification(
                ActorKind.AUTOMATED,
                (
                    f"{ratio:.0%} identical to an earlier message from the same "
                    f"dealer — a repeated template, not a new reply."
                ),
                0.8,
            )

    # Positive evidence of a person: a price, a direct answer, or a named signature.
    signals: list[str] = []
    if re.search(r"\$\s?\d", normalized_text):
        signals.append("quotes a figure")
    if re.search(r"\b(vin|stock ?#|mileage|miles)\b", normalized_text, re.IGNORECASE):
        signals.append("cites specifics about the car")
    if message.from_name and " " in message.from_name.strip():
        signals.append(f"signed by a named person ({message.from_name})")
    if re.search(r"\b(I|we)\b.{0,60}\b(checked|spoke|asked|confirmed|took)\b", normalized_text):
        signals.append("describes an action the sender took")

    if len(signals) >= 2:
        return Classification(
            ActorKind.HUMAN,
            "Looks written by a person: " + "; ".join(signals) + ".",
            0.75,
        )
    if signals:
        return Classification(
            ActorKind.UNKNOWN,
            "Weak evidence of a person (" + signals[0] + "); not conclusive.",
            0.5,
        )
    return Classification(
        ActorKind.UNKNOWN,
        "No signal either way — generic wording, no figures, no named sender.",
        0.4,
    )
