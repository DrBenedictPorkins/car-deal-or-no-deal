from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import DbSession, get_dealer_or_404
from app.models import Contact, Dealer, NegotiationStateDef, StateTransition, Vehicle
from app.schemas.entities import (
    ContactIn,
    ContactOut,
    ContactPatch,
    DealerIn,
    DealerOut,
    DealerPatch,
    StateDefOut,
    StateSet,
    StateTransitionOut,
    VehicleIn,
    VehicleOut,
    VehiclePatch,
)
from app.services import state_engine

router = APIRouter(prefix="/api", tags=["dealers"])


@router.get("/states", response_model=list[StateDefOut])
def list_states(db: DbSession):
    return db.scalars(
        select(NegotiationStateDef).order_by(NegotiationStateDef.sort_order)
    ).all()


@router.get("/dealers", response_model=list[DealerOut])
def list_dealers(db: DbSession):
    return db.scalars(select(Dealer).order_by(Dealer.name)).all()


@router.post("/dealers", response_model=DealerOut, status_code=201)
def create_dealer(payload: DealerIn, db: DbSession):
    dealer = Dealer(**payload.model_dump())
    db.add(dealer)
    db.flush()
    return dealer


@router.get("/dealers/{dealer_id}", response_model=DealerOut)
def read_dealer(dealer_id: int, db: DbSession):
    return get_dealer_or_404(db, dealer_id)


@router.patch("/dealers/{dealer_id}", response_model=DealerOut)
def update_dealer(dealer_id: int, payload: DealerPatch, db: DbSession):
    dealer = get_dealer_or_404(db, dealer_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(dealer, key, value)
    db.flush()
    return dealer


@router.delete("/dealers/{dealer_id}", status_code=204)
def delete_dealer(dealer_id: int, db: DbSession) -> None:
    dealer = get_dealer_or_404(db, dealer_id)
    db.delete(dealer)


@router.put("/dealers/{dealer_id}/state", response_model=StateTransitionOut)
def set_state(dealer_id: int, payload: StateSet, db: DbSession):
    dealer = get_dealer_or_404(db, dealer_id)
    if db.get(NegotiationStateDef, payload.state_code) is None:
        raise HTTPException(status_code=400, detail=f"unknown state {payload.state_code}")
    return state_engine.set_state_manually(
        db, dealer, payload.state_code, reason=payload.reason, pin=payload.pin
    )


@router.post("/dealers/{dealer_id}/state/unpin", response_model=DealerOut)
def unpin_state(dealer_id: int, db: DbSession):
    """Hand the dealer back to the rules engine."""
    dealer = get_dealer_or_404(db, dealer_id)
    dealer.state_is_pinned = False
    db.flush()
    state_engine.refresh_one(db, dealer_id)
    db.refresh(dealer)
    return dealer


@router.get("/dealers/{dealer_id}/transitions", response_model=list[StateTransitionOut])
def list_transitions(dealer_id: int, db: DbSession):
    return db.scalars(
        select(StateTransition)
        .where(StateTransition.dealer_id == dealer_id)
        .order_by(StateTransition.created_at.desc())
    ).all()


# ----------------------------------------------------------------- contacts
@router.get("/contacts", response_model=list[ContactOut])
def list_contacts(db: DbSession, dealer_id: int | None = None):
    q = select(Contact)
    if dealer_id is not None:
        q = q.where(Contact.dealer_id == dealer_id)
    return db.scalars(q.order_by(Contact.name)).all()


@router.post("/contacts", response_model=ContactOut, status_code=201)
def create_contact(payload: ContactIn, db: DbSession):
    get_dealer_or_404(db, payload.dealer_id)
    contact = Contact(**payload.model_dump())
    db.add(contact)
    db.flush()
    return contact


@router.patch("/contacts/{contact_id}", response_model=ContactOut)
def update_contact(contact_id: int, payload: ContactPatch, db: DbSession):
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="contact not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(contact, key, value)
    db.flush()
    return contact


@router.delete("/contacts/{contact_id}", status_code=204)
def delete_contact(contact_id: int, db: DbSession) -> None:
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="contact not found")
    db.delete(contact)


# ----------------------------------------------------------------- vehicles
@router.get("/vehicles", response_model=list[VehicleOut])
def list_vehicles(db: DbSession, dealer_id: int | None = None):
    q = select(Vehicle)
    if dealer_id is not None:
        q = q.where(Vehicle.dealer_id == dealer_id)
    return db.scalars(q.order_by(Vehicle.id)).all()


@router.post("/vehicles", response_model=VehicleOut, status_code=201)
def create_vehicle(payload: VehicleIn, db: DbSession):
    get_dealer_or_404(db, payload.dealer_id)
    vehicle = Vehicle(**payload.model_dump())
    db.add(vehicle)
    db.flush()
    return vehicle


@router.patch("/vehicles/{vehicle_id}", response_model=VehicleOut)
def update_vehicle(vehicle_id: int, payload: VehiclePatch, db: DbSession):
    vehicle = db.get(Vehicle, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="vehicle not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(vehicle, key, value)
    db.flush()
    return vehicle


@router.delete("/vehicles/{vehicle_id}", status_code=204)
def delete_vehicle(vehicle_id: int, db: DbSession) -> None:
    vehicle = db.get(Vehicle, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="vehicle not found")
    db.delete(vehicle)
