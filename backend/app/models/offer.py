from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums import Confidence, FeeKind
from app.models.base import Base, utcnow

if TYPE_CHECKING:
    from app.models.dealer import Dealer


class Offer(Base):
    """A dealer quote. Versioned and never replaced — history is the product."""

    __tablename__ = "offer"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dealer_id: Mapped[int] = mapped_column(
        ForeignKey("dealer.id", ondelete="CASCADE"), nullable=False
    )
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicle.id", ondelete="SET NULL"))
    interaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("interaction.id", ondelete="SET NULL")
    )
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaign.id", ondelete="SET NULL"))

    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    supersedes_offer_id: Mapped[int | None] = mapped_column(
        ForeignKey("offer.id", ondelete="SET NULL")
    )
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)

    quoted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # --- money (integer cents, always) ------------------------------------
    msrp_cents: Mapped[int | None] = mapped_column(Integer)
    advertised_price_cents: Mapped[int | None] = mapped_column(Integer)
    selling_price_cents: Mapped[int | None] = mapped_column(Integer)
    # Stored only when the dealer *stated* a discount; also derivable.
    discount_cents: Mapped[int | None] = mapped_column(Integer)
    destination_cents: Mapped[int | None] = mapped_column(Integer)

    doc_fee_cents: Mapped[int | None] = mapped_column(Integer)
    processing_fee_cents: Mapped[int | None] = mapped_column(Integer)
    other_taxable_fees_cents: Mapped[int | None] = mapped_column(Integer)
    other_non_tax_fees_cents: Mapped[int | None] = mapped_column(Integer)

    tax_cents: Mapped[int | None] = mapped_column(Integer)
    registration_cents: Mapped[int | None] = mapped_column(Integer)
    title_fee_cents: Mapped[int | None] = mapped_column(Integer)

    # What the dealer *said* the out-the-door number is. Never trusted alone.
    quoted_otd_cents: Mapped[int | None] = mapped_column(Integer)

    # --- financing (tri-state booleans on purpose) -------------------------
    financing_required: Mapped[bool | None] = mapped_column(Boolean)
    financing_provider: Mapped[str | None] = mapped_column(String(120))
    apr_bp: Mapped[int | None] = mapped_column(Integer)
    financing_term_months: Mapped[int | None] = mapped_column(Integer)
    minimum_financed_cents: Mapped[int | None] = mapped_column(Integer)
    minimum_loan_months: Mapped[int | None] = mapped_column(Integer)
    # NULL means unconfirmed, and that distinction is the whole point of the
    # finance-then-pay-off analysis.
    prepayment_penalty: Mapped[bool | None] = mapped_column(Boolean)
    discount_clawback: Mapped[bool | None] = mapped_column(Boolean)
    clawback_terms: Mapped[str | None] = mapped_column(Text)

    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    deadline_note: Mapped[str | None] = mapped_column(String(300))

    confidence: Mapped[str] = mapped_column(String(10), default=Confidence.HIGH)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    dealer: Mapped[Dealer] = relationship(back_populates="offers")
    lines: Mapped[list[OfferLine]] = relationship(
        back_populates="offer", cascade="all, delete-orphan", order_by="OfferLine.id"
    )

    __table_args__ = (
        Index("ix_offer_dealer_version", "dealer_id", "version"),
        Index("ix_offer_current", "dealer_id", "is_current"),
    )


class OfferLine(Base):
    """Itemized add-ons and fees.

    ``add_ons_total`` is deliberately *not* a column on Offer: a stored total that can
    disagree with its parts is the exact failure this system exists to prevent.
    """

    __tablename__ = "offer_line"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    offer_id: Mapped[int] = mapped_column(
        ForeignKey("offer.id", ondelete="CASCADE"), nullable=False
    )

    kind: Mapped[str] = mapped_column(String(20), default=FeeKind.ADD_ON, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Drives normalization; overridable per line because A1 may be wrong.
    is_dealer_controlled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_taxable: Mapped[bool | None] = mapped_column(Boolean)

    mandatory_claimed: Mapped[bool | None] = mapped_column(Boolean)
    already_installed: Mapped[bool | None] = mapped_column(Boolean)
    user_wants: Mapped[bool] = mapped_column(Boolean, default=False)
    removable_confirmed: Mapped[bool | None] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(Text)

    offer: Mapped[Offer] = relationship(back_populates="lines")
