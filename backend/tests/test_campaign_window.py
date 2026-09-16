"""The sweep window.

Anchored on when the buyer first reached out, never rolling. The oldest messages
are the opening offers everything else is measured against, so a window that
moves would quietly delete the thing that makes "improved by $806.21" computable.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.models import Campaign
from app.models.base import utcnow
from app.services import inbox as inbox_service


def test_quick_picks_are_offered(client):
    assert client.get("/api/campaigns/quick-picks").json() == [3, 7, 14, 30]


def test_a_campaign_can_be_started_from_a_rough_answer(client):
    created = client.post(
        "/api/campaigns", json={"name": "Civic", "days_ago": 7}
    ).json()
    opened = datetime.fromisoformat(created["opened_at"])
    assert 6 <= (utcnow() - opened).days <= 8
    # Midnight, so "7 days ago" covers the whole of that day.
    assert (opened.hour, opened.minute, opened.second) == (0, 0, 0)


def test_an_exact_date_wins_over_the_quick_pick(client):
    created = client.post(
        "/api/campaigns",
        json={"name": "Civic", "days_ago": 7, "opened_at": "2026-08-30T00:00:00"},
    ).json()
    assert created["opened_at"].startswith("2026-08-30")


def test_a_campaign_with_no_answer_starts_today(client):
    created = client.post("/api/campaigns", json={"name": "Civic"}).json()
    assert created["opened_at"] is not None


def test_the_start_date_can_be_changed_afterwards(client):
    created = client.post("/api/campaigns", json={"name": "Civic", "days_ago": 7}).json()
    moved = client.patch(
        f"/api/campaigns/{created['id']}", json={"days_ago": 30}
    ).json()
    assert datetime.fromisoformat(moved["opened_at"]) < datetime.fromisoformat(
        created["opened_at"]
    )


def test_the_sweep_anchors_on_the_active_campaign(db):
    opened = utcnow() - timedelta(days=9)
    db.add(Campaign(name="Civic", opened_at=opened, status="ACTIVE"))
    db.flush()
    assert inbox_service.default_since(db) == opened


def test_a_closed_campaign_does_not_anchor_the_sweep(db):
    db.add(
        Campaign(
            name="Old", opened_at=utcnow() - timedelta(days=400), status="CLOSED"
        )
    )
    db.flush()
    assert inbox_service.default_since(db) is None


def test_sweeping_without_a_start_date_is_refused(client, db):
    """There is no path that quietly scans an entire mailbox."""
    response = client.post("/api/inbox/sweep", json={})
    assert response.status_code == 400
    assert "unbounded" in response.json()["detail"]


def test_a_days_ago_override_works_without_a_campaign(client, db):
    response = client.post("/api/inbox/sweep", json={"days_ago": 7})
    assert response.status_code == 200
    assert response.json()["since"] is not None
