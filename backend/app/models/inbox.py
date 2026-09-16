"""Staged inbound mail that has not been claimed yet.

Kept out of ``interaction`` on purpose. An Interaction is part of a negotiation;
these are just messages that arrived in a window of time. Mixing them would mean
every negotiation query carried a "…and this one is real" filter, and deleting
inbox noise would touch the negotiation history.

**Metadata only.** Subject, sender, date and the provider's own snippet are stored;
the body is not fetched until the user promotes the message. That is what makes it
safe to sweep a date range broadly — nothing personal lands on disk unless you
point at it and say "this one matters".
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.enums import InboxStatus
from app.models.base import Base, utcnow


class InboxMessage(Base):
    __tablename__ = "inbox_message"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaign.id", ondelete="CASCADE"))

    source_system: Mapped[str] = mapped_column(String(40), default="gmail", nullable=False)
    source_identifier: Mapped[str] = mapped_column(String(300), nullable=False)
    thread_identifier: Mapped[str | None] = mapped_column(String(300))
    rfc822_message_id: Mapped[str | None] = mapped_column(String(400))

    from_name: Mapped[str | None] = mapped_column(String(200))
    from_email: Mapped[str | None] = mapped_column(String(320))
    to_emails: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(String(500))
    # The provider's own short preview. Enough to recognise a message in a list
    # without fetching what it actually says.
    snippet: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    label_ids: Mapped[str | None] = mapped_column(String(500))
    has_attachments: Mapped[bool] = mapped_column(Boolean, default=False)

    status: Mapped[str] = mapped_column(String(20), default=InboxStatus.NEW, nullable=False)
    # Set once promoted, so the inbox row keeps a pointer to what it became.
    promoted_interaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("interaction.id", ondelete="SET NULL")
    )
    dealer_id: Mapped[int | None] = mapped_column(ForeignKey("dealer.id", ondelete="SET NULL"))

    # Deterministic ranking so likely dealership mail floats up. Suggesting an
    # ordering is safe in a way that suggesting an action is not.
    score: Mapped[float] = mapped_column(Float, default=0.0)
    score_reasons: Mapped[str | None] = mapped_column(Text)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_inbox_source", "source_system", "source_identifier", unique=True),
        Index("ix_inbox_status", "status", "sent_at"),
        Index("ix_inbox_thread", "thread_identifier"),
        Index("ix_inbox_from", "from_email"),
    )

    @property
    def reasons(self) -> list[str]:
        return [r for r in (self.score_reasons or "").split("\n") if r]
