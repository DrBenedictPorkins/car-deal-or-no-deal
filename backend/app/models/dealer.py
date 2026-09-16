from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums import ActorKind, ContactRole, DomainKind, VehicleCondition
from app.models.base import Base, TimestampMixin, utcnow

if TYPE_CHECKING:
    from app.models.interaction import Interaction
    from app.models.offer import Offer


class NegotiationStateDef(Base):
    """The state catalogue.

    States are *data* so the workflow can evolve without a migration (the brief is
    explicit about not hardcoding workflow assumptions).
    """

    __tablename__ = "negotiation_state"

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    is_terminal: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active_pipeline: Mapped[bool] = mapped_column(Boolean, default=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=True)


class Dealer(Base, TimestampMixin):
    __tablename__ = "dealer"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaign.id", ondelete="SET NULL"))

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    address: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(120))
    state: Mapped[str | None] = mapped_column(String(2))
    postal_code: Mapped[str | None] = mapped_column(String(10))
    website: Mapped[str | None] = mapped_column(String(300))
    phone: Mapped[str | None] = mapped_column(String(50))

    # The plus-alias used when submitting this dealership's web form, e.g.
    # "you+dl-westport@gmail.com". The one identity signal the buyer controls,
    # and it survives whatever domain the CRM decides to reply from.
    inquiry_alias: Mapped[str | None] = mapped_column(String(320))

    distance_miles: Mapped[float | None] = mapped_column(Numeric(7, 1))
    is_local: Mapped[bool] = mapped_column(Boolean, default=False)

    state_code: Mapped[str] = mapped_column(
        ForeignKey("negotiation_state.code"), default="DISCOVERED", nullable=False
    )
    state_is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    state_changed_at: Mapped[datetime | None] = mapped_column(DateTime)
    first_contacted_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Record lifecycle, distinct from negotiation state.
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    notes: Mapped[str | None] = mapped_column(Text)

    domains: Mapped[list[DealerDomain]] = relationship(
        back_populates="dealer", cascade="all, delete-orphan", lazy="selectin"
    )
    contacts: Mapped[list[Contact]] = relationship(
        back_populates="dealer", cascade="all, delete-orphan"
    )
    vehicles: Mapped[list[Vehicle]] = relationship(
        back_populates="dealer", cascade="all, delete-orphan"
    )
    interactions: Mapped[list[Interaction]] = relationship(
        back_populates="dealer", cascade="all, delete-orphan"
    )
    offers: Mapped[list[Offer]] = relationship(
        back_populates="dealer", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_dealer_state", "state_code"),
        Index("ix_dealer_alias", "inquiry_alias"),
    )


class Contact(Base, TimestampMixin):
    __tablename__ = "contact"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dealer_id: Mapped[int] = mapped_column(
        ForeignKey("dealer.id", ondelete="CASCADE"), nullable=False
    )

    name: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(50))
    role: Mapped[str] = mapped_column(String(40), default=ContactRole.UNKNOWN)

    actor_kind: Mapped[str] = mapped_column(String(20), default=ActorKind.UNKNOWN)
    # A classification without a stated reason is not acceptable.
    automation_evidence: Mapped[str | None] = mapped_column(Text)

    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)

    dealer: Mapped[Dealer] = relationship(back_populates="contacts")

    __table_args__ = (Index("ix_contact_email", "email"),)


class Vehicle(Base, TimestampMixin):
    __tablename__ = "vehicle"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dealer_id: Mapped[int] = mapped_column(
        ForeignKey("dealer.id", ondelete="CASCADE"), nullable=False
    )
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaign.id", ondelete="SET NULL"))

    year: Mapped[int | None] = mapped_column(Integer)
    make: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(120))
    trim: Mapped[str | None] = mapped_column(String(120))
    body_style: Mapped[str | None] = mapped_column(String(80))
    vin: Mapped[str | None] = mapped_column(String(17))
    stock_number: Mapped[str | None] = mapped_column(String(60))
    exterior_color: Mapped[str | None] = mapped_column(String(80))
    interior_color: Mapped[str | None] = mapped_column(String(80))
    mileage: Mapped[int | None] = mapped_column(Integer)
    msrp_cents: Mapped[int | None] = mapped_column(Integer)
    condition: Mapped[str] = mapped_column(String(20), default=VehicleCondition.NEW)

    # Tri-state on purpose: NULL means "never asked", False means the dealer said no.
    is_demo: Mapped[bool | None] = mapped_column(Boolean)
    is_loaner: Mapped[bool | None] = mapped_column(Boolean)
    damage_history: Mapped[str | None] = mapped_column(Text)
    availability: Mapped[str | None] = mapped_column(String(80))
    notes: Mapped[str | None] = mapped_column(Text)

    dealer: Mapped[Dealer] = relationship(back_populates="vehicles")

    __table_args__ = (Index("ix_vehicle_vin", "vin"),)


class DealerDomain(Base):
    """A mail domain known to belong to a dealership.

    A dealership routinely has more than one: the store's own domain for
    salespeople, a separate one for management, and whatever domain their CRM
    sends from. These are *learned* — folding a message from an unrecognized
    domain into an existing dealership records it, so the domain list fills
    itself in and never has to be typed.

    Deliberately not unique on ``domain`` alone: a dealer group legitimately
    shares one domain across several rooftops, and resolution handles that by
    refusing to guess rather than by pretending it cannot happen.
    """

    __tablename__ = "dealer_domain"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dealer_id: Mapped[int] = mapped_column(
        ForeignKey("dealer.id", ondelete="CASCADE"), nullable=False
    )
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default=DomainKind.UNKNOWN)
    # Which message taught us this, so the domain list has provenance like
    # everything else the system believes.
    learned_from_interaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("interaction.id", ondelete="SET NULL")
    )
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    dealer: Mapped[Dealer] = relationship(back_populates="domains")

    __table_args__ = (
        UniqueConstraint("dealer_id", "domain", name="uq_dealer_domain"),
        Index("ix_dealer_domain_domain", "domain"),
    )
