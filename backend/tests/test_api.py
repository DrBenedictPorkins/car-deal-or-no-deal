"""API surface: the endpoints the dashboard actually calls."""

from __future__ import annotations

from conftest import make_dealer


def test_health_reports_whether_anything_leaves_the_machine(client):
    body = client.get("/api/system/health").json()
    assert body["status"] == "ok"
    assert body["llm_enabled"] is False
    assert body["llm_provider"] == "null"


def test_profile_round_trip(client):
    created = client.get("/api/profile").json()
    assert created["id"] == 1

    payload = {
        **{k: v for k, v in created.items() if k not in ("id", "created_at", "updated_at")},
        "registration_state": "PA",
        "zip_code": "18435",
        "expected_tax_rate_bp": 600,
        "excluded_colors": "white",
        "local_dealer_premium_cents": 30000,
    }
    updated = client.put("/api/profile", json=payload).json()
    assert updated["expected_tax_rate_bp"] == 600
    assert updated["excluded_colors"] == "white"


def test_dealer_lifecycle(client):
    created = client.post(
        "/api/dealers",
        json={"name": "Honda of Stamford", "city": "Stamford", "state": "CT", "is_local": True},
    ).json()
    assert created["state_code"] == "DISCOVERED"

    patched = client.patch(f"/api/dealers/{created['id']}", json={"distance_miles": 41}).json()
    assert patched["distance_miles"] == 41

    assert client.get("/api/dealers").json()[0]["name"] == "Honda of Stamford"
    assert client.delete(f"/api/dealers/{created['id']}").status_code == 204
    assert client.get(f"/api/dealers/{created['id']}").status_code == 404


def test_offer_creation_returns_computed_pricing(client, db):
    dealer = make_dealer(db)
    db.commit()

    response = client.post(
        "/api/offers",
        json={
            "dealer_id": dealer.id,
            "quoted_at": "2026-08-30T16:03:00",
            "msrp_cents": 2909000,
            "selling_price_cents": 2732942,
            "doc_fee_cents": 69900,
            "other_non_tax_fees_cents": 36800,
            "tax_cents": 170559,
            "quoted_otd_cents": 3050001,
            "lines": [
                {"kind": "ADD_ON", "name": "VIN Etching", "price_cents": 26900,
                 "is_taxable": True},
                {"kind": "ADD_ON", "name": "Wheel Locks", "price_cents": 12900,
                 "is_taxable": True},
            ],
        },
    )
    assert response.status_code == 201
    pricing = response.json()["pricing"]
    assert pricing["dealer_controlled_cents"] == 2842642
    assert pricing["computed_otd_cents"] == 3050001
    assert pricing["otd_variance_cents"] == 0
    assert pricing["discount_from_msrp_cents"] == 176058


def test_state_can_be_pinned_and_released(client, db):
    dealer = make_dealer(db)
    db.commit()

    transition = client.put(
        f"/api/dealers/{dealer.id}/state", json={"state_code": "FINALIST", "reason": "my call"}
    ).json()
    assert transition["to_state"] == "FINALIST"
    assert client.get(f"/api/dealers/{dealer.id}").json()["state_is_pinned"] is True

    released = client.post(f"/api/dealers/{dealer.id}/state/unpin").json()
    assert released["state_is_pinned"] is False


def test_unknown_state_is_rejected(client, db):
    dealer = make_dealer(db)
    db.commit()
    response = client.put(
        f"/api/dealers/{dealer.id}/state", json={"state_code": "MADE_UP"}
    )
    assert response.status_code == 400


def test_fact_provenance_endpoint_answers_why(client, db):
    dealer = make_dealer(db)
    db.commit()
    interaction = client.post(
        "/api/interactions",
        json={
            "dealer_id": dealer.id,
            "channel": "EMAIL",
            "direction": "INBOUND",
            "occurred_at": "2026-08-30T14:03:00",
            "raw_content": "Brand new, not a demo.",
        },
    ).json()

    fact = client.post(
        "/api/facts",
        json={
            "subject_type": "VEHICLE",
            "subject_id": 1,
            "attribute": "vehicle.is_demo",
            "dealer_id": dealer.id,
            "value_bool": False,
            "interaction_id": interaction["id"],
            "quote": "Brand new, not a demo.",
        },
    ).json()

    provenance = client.get(f"/api/facts/{fact['id']}/provenance").json()
    assert provenance["fact"]["display_value"] == "No"
    assert provenance["interaction"]["raw_content"] == "Brand new, not a demo."


def test_drafts_support_edit_approve_discard_but_not_send(client, db):
    dealer = make_dealer(db)
    db.commit()

    draft = client.post(
        "/api/drafts", json={"dealer_id": dealer.id, "body": "Hello", "subject": "Pricing"}
    ).json()
    assert draft["status"] == "DRAFT"

    edited = client.patch(f"/api/drafts/{draft['id']}", json={"body": "Hello again"}).json()
    assert edited["edited_by_user"] is True

    approved = client.patch(f"/api/drafts/{draft['id']}", json={"status": "APPROVED"}).json()
    assert approved["status"] == "APPROVED"
    assert approved["approved_at"]

    # SENT is never settable by hand — sending goes through the endpoint that
    # enforces the outbound allowlist and records the provider message id.
    blocked = client.patch(f"/api/drafts/{draft['id']}", json={"status": "SENT"})
    assert blocked.status_code == 400
    assert "/api/ingest/drafts" in blocked.json()["detail"]


def test_generated_draft_cites_the_competing_number(client, db):
    local = make_dealer(db, "Honda of Stamford", is_local=True)
    rival = make_dealer(db, "Honda of Westport")
    db.commit()

    for dealer_id, otd in ((local.id, 3090000), (rival.id, 3077510)):
        client.post(
            "/api/offers",
            json={
                "dealer_id": dealer_id,
                "quoted_at": "2026-08-30T16:03:00",
                "selling_price_cents": 2800000,
                "tax_cents": 168000,
                "quoted_otd_cents": otd,
            },
        )

    draft = client.post(f"/api/dealers/{local.id}/drafts/generate").json()
    assert "$30,775.10" in draft["body"]
    assert "buy locally" in draft["body"]
    assert draft["rationale"]


def test_dashboard_and_comparison_render(client, db):
    make_dealer(db, "Honda of Stamford")
    db.commit()
    assert client.get("/api/dashboard").status_code == 200
    assert client.get("/api/compare").status_code == 200
    assert client.get("/api/states").json()[0]["code"] == "DISCOVERED"


def test_refresh_is_idempotent(client, db):
    make_dealer(db, "Honda of Stamford")
    db.commit()
    first = client.post("/api/system/refresh").json()
    second = client.post("/api/system/refresh").json()
    assert second["notifications"] == 0
    assert second["signals_added"] == 0
    assert first["state_transitions"] >= 0


def test_canned_questions_are_discoverable_and_runnable(client, db):
    make_dealer(db)
    db.commit()
    keys = client.get("/api/questions/canned").json()
    assert "best_offer" in keys
    for key in keys:
        assert client.get(f"/api/ask/{key}").status_code == 200
    assert client.get("/api/ask/nonsense").status_code == 404
