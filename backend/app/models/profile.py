from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.enums import FinancingStance, PurchaseType
from app.models.base import Base, TimestampMixin


class BuyerProfile(Base, TimestampMixin):
    """The buyer's negotiation profile. Logically a single row (id=1)."""

    __tablename__ = "buyer_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    display_name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(50))

    purchase_type: Mapped[str] = mapped_column(String(20), default=PurchaseType.NEW_ONLY)
    target_year: Mapped[int | None] = mapped_column(Integer)
    target_make: Mapped[str | None] = mapped_column(String(80))
    target_model: Mapped[str | None] = mapped_column(String(120))
    target_trim: Mapped[str | None] = mapped_column(String(120))
    target_powertrain: Mapped[str | None] = mapped_column(String(60))

    color_preferences: Mapped[str | None] = mapped_column(Text)
    excluded_colors: Mapped[str | None] = mapped_column(Text)

    has_trade_in: Mapped[bool] = mapped_column(Boolean, default=False)
    cash_available: Mapped[bool] = mapped_column(Boolean, default=True)
    financing_acceptable: Mapped[str] = mapped_column(
        String(30), default=FinancingStance.ONLY_IF_ADVANTAGEOUS
    )

    wants_add_ons: Mapped[bool] = mapped_column(Boolean, default=False)
    wants_maintenance_plan: Mapped[bool] = mapped_column(Boolean, default=False)

    registration_state: Mapped[str | None] = mapped_column(String(2))
    zip_code: Mapped[str | None] = mapped_column(String(10))
    # Basis points keeps the implied-tax-rate check in exact integer arithmetic.
    expected_tax_rate_bp: Mapped[int | None] = mapped_column(Integer)

    max_distance_miles: Mapped[int | None] = mapped_column(Integer)
    # How much more a local dealer may charge and still be the better choice.
    local_dealer_premium_cents: Mapped[int] = mapped_column(Integer, default=0)

    contact_preference: Mapped[str] = mapped_column(String(40), default="EMAIL_PREFERRED")
    avoid_phone_calls: Mapped[bool] = mapped_column(Boolean, default=True)
    avoid_dealership_visits: Mapped[bool] = mapped_column(Boolean, default=True)

    follow_up_after_hours: Mapped[int] = mapped_column(Integer, default=48)
    no_response_after_days: Mapped[int] = mapped_column(Integer, default=7)

    notes: Mapped[str | None] = mapped_column(Text)


class Campaign(Base, TimestampMixin):
    """One shopping effort. Nullable everywhere in Phase 1 (assumption A2)."""

    __tablename__ = "campaign"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    target_description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    # When the buyer first reached out — the anchor for the inbox sweep. Not a
    # rolling window: the oldest messages are the opening offers, and everything
    # else is measured against them, so they must never scroll out of scope.
    opened_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    notes: Mapped[str | None] = mapped_column(Text)

    # Declared but unused in Phase 1; keeps the column available to later phases.
    target_max_price_cents: Mapped[int | None] = mapped_column(Integer)
