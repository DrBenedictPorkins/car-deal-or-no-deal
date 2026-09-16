"""Hard enumerations.

These describe *physics* (a message is inbound or it isn't) and so live in code.
Workflow enumerations that the user expects to evolve — negotiation states — live in
the ``negotiation_state`` catalogue table instead. See DATA_MODEL.md.
"""

from __future__ import annotations

from enum import StrEnum


class Channel(StrEnum):
    EMAIL = "EMAIL"
    # Most dealerships publish no address at all — you fill in a form on their site
    # and give a reply-to. That submission is a real outbound contact and starts the
    # clock, so it needs to be an interaction like any other.
    WEB_FORM = "WEB_FORM"
    CALL = "CALL"
    SMS = "SMS"
    IN_PERSON = "IN_PERSON"
    NOTE = "NOTE"
    DOCUMENT = "DOCUMENT"


class Direction(StrEnum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"
    INTERNAL = "INTERNAL"


class ActorKind(StrEnum):
    HUMAN = "HUMAN"
    AUTOMATED = "AUTOMATED"
    UNKNOWN = "UNKNOWN"


class Party(StrEnum):
    BUYER = "BUYER"
    DEALER = "DEALER"
    NOBODY = "NOBODY"


class ContactRole(StrEnum):
    SALESPERSON = "SALESPERSON"
    SALES_MANAGER = "SALES_MANAGER"
    INTERNET_SALES = "INTERNET_SALES"
    BDC = "BDC"
    CLIENT_SERVICES = "CLIENT_SERVICES"
    FINANCE_MANAGER = "FINANCE_MANAGER"
    GENERAL_MANAGER = "GENERAL_MANAGER"
    AUTOMATED_ASSISTANT = "AUTOMATED_ASSISTANT"
    UNKNOWN = "UNKNOWN"


class VehicleCondition(StrEnum):
    NEW = "NEW"
    USED = "USED"
    CERTIFIED = "CERTIFIED"


class FeeKind(StrEnum):
    ADD_ON = "ADD_ON"
    DEALER_FEE = "DEALER_FEE"
    GOVERNMENT = "GOVERNMENT"
    OTHER = "OTHER"


class FactStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    CURRENT = "CURRENT"
    SUPERSEDED = "SUPERSEDED"
    DISPUTED = "DISPUTED"
    RETRACTED = "RETRACTED"


class ExtractionMethod(StrEnum):
    MANUAL = "MANUAL"
    RULE = "RULE"
    LLM = "LLM"
    IMPORT = "IMPORT"


class SubjectType(StrEnum):
    DEALER = "DEALER"
    CONTACT = "CONTACT"
    VEHICLE = "VEHICLE"
    OFFER = "OFFER"
    DEAL = "DEAL"


class QuestionStatus(StrEnum):
    OPEN = "OPEN"
    ANSWERED = "ANSWERED"
    IGNORED = "IGNORED"
    WITHDRAWN = "WITHDRAWN"


class CommitmentStatus(StrEnum):
    OPEN = "OPEN"
    KEPT = "KEPT"
    BROKEN = "BROKEN"
    EXPIRED = "EXPIRED"
    WAIVED = "WAIVED"


class ContradictionKind(StrEnum):
    VALUE_CONFLICT = "VALUE_CONFLICT"
    NUMERIC_CONFLICT = "NUMERIC_CONFLICT"
    PRESENCE_CONFLICT = "PRESENCE_CONFLICT"
    PROMISE_BROKEN = "PROMISE_BROKEN"


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class ContradictionStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class BehaviorDimension(StrEnum):
    TRANSPARENCY = "TRANSPARENCY"
    FRICTION = "FRICTION"
    RESPONSIVENESS = "RESPONSIVENESS"
    PRICE_COMPETITIVENESS = "PRICE_COMPETITIVENESS"


class DraftStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    DISCARDED = "DISCARDED"
    SENT = "SENT"
    FAILED = "FAILED"


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class PurchaseType(StrEnum):
    NEW_ONLY = "NEW_ONLY"
    NEW_OR_USED = "NEW_OR_USED"
    USED_ONLY = "USED_ONLY"


class FinancingStance(StrEnum):
    NEVER = "NEVER"
    ONLY_IF_ADVANTAGEOUS = "ONLY_IF_ADVANTAGEOUS"
    PREFERRED = "PREFERRED"


class InboxStatus(StrEnum):
    NEW = "NEW"
    PROMOTED = "PROMOTED"
    IGNORED = "IGNORED"


class DomainKind(StrEnum):
    PRIMARY = "PRIMARY"
    CRM = "CRM"
    MANAGEMENT = "MANAGEMENT"
    GROUP = "GROUP"
    UNKNOWN = "UNKNOWN"


class NotificationType(StrEnum):
    NEW_OFFER = "NEW_OFFER"
    DEALER_RESPONDED = "DEALER_RESPONDED"
    NO_RESPONSE_24H = "NO_RESPONSE_24H"
    NEW_BEST_OFFER = "NEW_BEST_OFFER"
    PRICE_CHANGED = "PRICE_CHANGED"
    NEW_ADD_ON = "NEW_ADD_ON"
    DEADLINE_APPROACHING = "DEADLINE_APPROACHING"
    CONTRADICTION_DETECTED = "CONTRADICTION_DETECTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
