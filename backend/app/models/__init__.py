"""SQLAlchemy models. Importing this package registers every table on Base.metadata."""

from app.models.base import Base, TimestampMixin, utcnow
from app.models.comms import DraftMessage, LLMRun, Notification, SyncState
from app.models.dealer import (
    Contact,
    Dealer,
    DealerDomain,
    NegotiationStateDef,
    Vehicle,
)
from app.models.inbox import InboxMessage
from app.models.interaction import (
    Document,
    EmailSource,
    Interaction,
    TranscriptSegment,
    TranscriptSource,
)
from app.models.negotiation import (
    BehaviorSignal,
    Commitment,
    Contradiction,
    Fact,
    Question,
    StateTransition,
)
from app.models.offer import Offer, OfferLine
from app.models.profile import BuyerProfile, Campaign

__all__ = [
    "Base",
    "BehaviorSignal",
    "BuyerProfile",
    "Campaign",
    "Commitment",
    "Contact",
    "Contradiction",
    "Dealer",
    "DealerDomain",
    "Document",
    "DraftMessage",
    "EmailSource",
    "InboxMessage",
    "Fact",
    "Interaction",
    "LLMRun",
    "NegotiationStateDef",
    "Notification",
    "Offer",
    "OfferLine",
    "Question",
    "StateTransition",
    "SyncState",
    "TimestampMixin",
    "TranscriptSegment",
    "TranscriptSource",
    "Vehicle",
    "utcnow",
]
