# PROMPT: Write FastAPI TestClient tests for metrics conversion_rate zero, staff exclusion,
# idempotent ingest, funnel REENTRY dedup, malformed ingest returns 200 with errors.
# CHANGES MADE: Patched utc_today to 2026-04-10 in conftest; ingest via raw JSON body.

"""API metrics and ingestion tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.helpers import make_event


STORE_ID = "STORE_BLR_002"


def test_metrics_conversion_rate_zero_when_no_purchases(client):
    for i in range(3):
        client.post(
            "/events/ingest",
            json={
                "events": [
                    make_event(
                        event_id=f"entry-{i}",
                        visitor_id=f"VIS_visitor{i}",
                        event_type="ENTRY",
                        timestamp=f"2026-04-10T1{i}:00:00Z",
                    )
                ]
            },
        )

    response = client.get(f"/stores/{STORE_ID}/metrics")
    assert response.status_code == 200
    data = response.json()
    assert data["conversion_rate"] == 0.0
    assert data["unique_visitors"] == 3


def test_metrics_excludes_staff_from_unique_visitors(client):
    client.post(
        "/events/ingest",
        json={
            "events": [
                make_event(
                    event_id="staff-entry-1",
                    visitor_id="VIS_staff01",
                    is_staff=True,
                ),
                make_event(
                    event_id="visitor-entry-1",
                    visitor_id="VIS_cust001",
                    is_staff=False,
                ),
            ]
        },
    )

    response = client.get(f"/stores/{STORE_ID}/metrics")
    data = response.json()
    assert data["unique_visitors"] == 1


def test_ingest_idempotent_same_ten_events(client):
    events = [
        make_event(event_id=f"dup-{i}", visitor_id=f"VIS_dup{i}") for i in range(10)
    ]
    first = client.post("/events/ingest", json={"events": events})
    second = client.post("/events/ingest", json={"events": events})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["inserted"] == 10
    assert second.json()["inserted"] == 0
    assert second.json()["skipped_duplicates"] == 10


def test_funnel_does_not_double_count_reentry(client):
    visitor = "VIS_funnel1"
    events = [
        make_event(event_id="funnel-entry", visitor_id=visitor, event_type="ENTRY"),
        make_event(
            event_id="funnel-reentry",
            visitor_id=visitor,
            event_type="REENTRY",
            timestamp="2026-04-10T14:00:00Z",
        ),
        make_event(
            event_id="funnel-zone",
            visitor_id=visitor,
            event_type="ZONE_ENTER",
            zone_id="ZONE_SKIN",
            timestamp="2026-04-10T14:05:00Z",
        ),
    ]
    client.post("/events/ingest", json={"events": events})
    response = client.get(f"/stores/{STORE_ID}/funnel")
    data = response.json()
    assert data["total_entries"]["count"] == 1


def test_malformed_event_returns_200_with_validation_errors(client):
    bad_event = make_event(event_id="bad-1")
    bad_event["confidence"] = 1.5
    response = client.post("/events/ingest", json={"events": [bad_event]})
    assert response.status_code == 200
    assert len(response.json()["validation_errors"]) == 1


def test_heatmap_endpoint(client):
    client.post(
        "/events/ingest",
        json={
            "events": [
                make_event(
                    event_id="heat-1",
                    event_type="ZONE_ENTER",
                    zone_id="ZONE_SKIN",
                )
            ]
        },
    )
    response = client.get(f"/stores/{STORE_ID}/heatmap")
    assert response.status_code == 200
    assert "zones" in response.json()


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert "status" in response.json()
