"""Test harness.

The database is redirected to a temp directory *before* the application imports, since
``app.db`` builds its engine at import time. Every test gets a freshly created schema.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="dealbench-test-"))
os.environ["DEALBENCH_DATA_DIR"] = str(_TMP)
os.environ["DEALBENCH_DATABASE_URL"] = f"sqlite+pysqlite:///{_TMP / 'test.sqlite3'}"
os.environ["DEALBENCH_LLM_ENABLED"] = "false"

from app.db import SessionLocal, engine  # noqa: E402
from app.enums import ActorKind, Channel, Direction  # noqa: E402
from app.models import Base, BuyerProfile, Contact, Dealer, Interaction, Vehicle  # noqa: E402
from app.services import offers as offer_service  # noqa: E402
from app.services.money import to_cents  # noqa: E402
from app.services.states import ensure_states  # noqa: E402

BASE_TIME = datetime(2026, 8, 30, 9, 0, 0)


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ANN001, ARG001
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture()
def db() -> Iterator:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    ensure_states(session)
    session.commit()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture()
def client(db) -> Iterator:  # noqa: ARG001 - db fixture prepares the schema
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


# ---------------------------------------------------------------- builders


@pytest.fixture()
def profile(db) -> BuyerProfile:
    row = BuyerProfile(
        id=1,
        registration_state="PA",
        zip_code="18435",
        expected_tax_rate_bp=600,
        wants_add_ons=False,
        local_dealer_premium_cents=to_cents("300.00"),
        follow_up_after_hours=48,
        no_response_after_days=7,
    )
    db.add(row)
    db.flush()
    return row


def make_dealer(db, name: str = "Test Honda", **kwargs) -> Dealer:
    dealer = Dealer(name=name, **kwargs)
    db.add(dealer)
    db.flush()
    return dealer


def make_contact(db, dealer: Dealer, name: str = "Pat Seller", **kwargs) -> Contact:
    contact = Contact(dealer_id=dealer.id, name=name, **kwargs)
    db.add(contact)
    db.flush()
    return contact


def make_vehicle(db, dealer: Dealer, **kwargs) -> Vehicle:
    vehicle = Vehicle(dealer_id=dealer.id, year=2026, make="Honda", model="Civic", **kwargs)
    db.add(vehicle)
    db.flush()
    return vehicle


def make_interaction(
    db,
    dealer: Dealer,
    *,
    direction: Direction = Direction.INBOUND,
    channel: Channel = Channel.EMAIL,
    days: float = 0,
    body: str = "hello",
    subject: str = "test",
    actor_kind: ActorKind = ActorKind.HUMAN,
    **kwargs,
) -> Interaction:
    row = Interaction(
        dealer_id=dealer.id,
        channel=channel,
        direction=direction,
        occurred_at=BASE_TIME + timedelta(days=days),
        subject=subject,
        raw_content=body,
        normalized_content=body,
        actor_kind=actor_kind,
        source_system="test",
        **kwargs,
    )
    db.add(row)
    db.flush()
    return row


def make_offer(db, dealer: Dealer, *, lines=None, days: float = 0, **fields):
    payload = {
        "dealer_id": dealer.id,
        "quoted_at": BASE_TIME + timedelta(days=days),
        **fields,
    }
    return offer_service.create(db, payload, lines or [])
