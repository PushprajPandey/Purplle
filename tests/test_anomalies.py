# PROMPT: Test BILLING_QUEUE_SPIKE at depth 6 (WARN), DEAD_ZONE after 31 min,
# CONVERSION_DROP when today is 25% below 7-day average.
# CHANGES MADE: Monkeypatch get_metrics for spike/drop tests; call _dead_zones directly
# with fixed datetime for dead zone test.

"""Anomaly detection tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.database import db_cursor, upsert_daily_conversion
from tests.helpers import make_event

STORE_ID = "STORE_BLR_002"


def _ingest_billing_joins(client, count: int, base_minute: int = 0) -> None:
    events = []
    for i in range(count):
        events.append(
            make_event(
                event_id=f"queue-join-{i}",
                visitor_id=f"VIS_queue{i}",
                event_type="BILLING_QUEUE_JOIN",
                zone_id="ZONE_BILLING",
                timestamp=f"2026-04-10T18:{base_minute + i:02d}:00Z",
                queue_depth=i + 1,
                sku_zone="billing",
            )
        )
    client.post("/events/ingest", json={"events": events})


def test_billing_queue_spike_warn_at_depth_6(client, monkeypatch):
    monkeypatch.setattr(
        "app.anomalies.get_metrics",
        lambda store_id: type(
            "M",
            (),
            {
                "current_queue_depth": 6,
                "conversion_rate": 0.5,
                "unique_visitors": 10,
                "avg_dwell_per_zone": {},
                "abandonment_rate": 0.0,
            },
        )(),
    )
    monkeypatch.setattr("app.anomalies.get_seven_day_avg_conversion", lambda s: 0.5)
    monkeypatch.setattr("app.anomalies._dead_zones", lambda *a, **k: [])

    from app.anomalies import get_anomalies

    anomalies = get_anomalies(STORE_ID)
    spike = [a for a in anomalies if a.anomaly_type == "BILLING_QUEUE_SPIKE"]
    assert len(spike) == 1
    assert spike[0].severity == "WARN"


def test_dead_zone_triggers_after_31_minutes(client, monkeypatch):
    now = datetime(2026, 4, 10, 15, 0, 0, tzinfo=timezone.utc)
    now_ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    old_ts = (now - timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%SZ")
    detected_at = now.isoformat().replace("+00:00", "Z")

    with db_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO events
            (event_id, store_id, camera_id, visitor_id, event_type, timestamp,
             zone_id, dwell_ms, is_staff, confidence, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "recent-zone-enter",
                STORE_ID,
                "CAM_2",
                "VIS_recent",
                "ZONE_ENTER",
                now_ts,
                "ZONE_BILLING",
                0,
                0,
                0.9,
                '{"queue_depth": null, "sku_zone": "billing", "session_seq": 1}',
            ),
        )
        cursor.execute(
            """
            INSERT INTO events
            (event_id, store_id, camera_id, visitor_id, event_type, timestamp,
             zone_id, dwell_ms, is_staff, confidence, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "old-zone-enter",
                STORE_ID,
                "CAM_2",
                "VIS_old",
                "ZONE_ENTER",
                old_ts,
                "ZONE_SKIN",
                0,
                0,
                0.9,
                '{"queue_depth": null, "sku_zone": "skin", "session_seq": 1}',
            ),
        )

    monkeypatch.setattr(
        "app.anomalies.get_metrics",
        lambda store_id: type(
            "M",
            (),
            {
                "current_queue_depth": 0,
                "conversion_rate": 0.3,
                "unique_visitors": 5,
                "avg_dwell_per_zone": {},
                "abandonment_rate": 0.0,
            },
        )(),
    )
    from app.anomalies import _dead_zones

    dead = _dead_zones(STORE_ID, detected_at)
    assert len(dead) >= 1


def test_conversion_drop_warn_at_25_percent_below_average(client, monkeypatch):
    monkeypatch.setattr(
        "app.anomalies.get_metrics",
        lambda store_id: type(
            "M",
            (),
            {
                "current_queue_depth": 0,
                "conversion_rate": 0.15,
                "unique_visitors": 20,
                "avg_dwell_per_zone": {},
                "abandonment_rate": 0.0,
            },
        )(),
    )
    monkeypatch.setattr("app.anomalies.get_seven_day_avg_conversion", lambda s: 0.20)
    monkeypatch.setattr("app.anomalies._billing_queue_spike", lambda *a, **k: [])
    monkeypatch.setattr("app.anomalies._dead_zones", lambda *a, **k: [])

    from app.anomalies import get_anomalies

    anomalies = get_anomalies(STORE_ID)
    drop = [a for a in anomalies if a.anomaly_type == "CONVERSION_DROP"]
    assert len(drop) == 1
    assert drop[0].severity == "WARN"


def test_billing_queue_spike_critical_above_10(client, monkeypatch):
    monkeypatch.setattr(
        "app.anomalies.get_metrics",
        lambda store_id: type(
            "M",
            (),
            {
                "current_queue_depth": 11,
                "conversion_rate": 0.5,
                "unique_visitors": 10,
                "avg_dwell_per_zone": {},
                "abandonment_rate": 0.0,
            },
        )(),
    )
    monkeypatch.setattr("app.anomalies.get_seven_day_avg_conversion", lambda s: 0.5)
    monkeypatch.setattr("app.anomalies._dead_zones", lambda *a, **k: [])

    from app.anomalies import get_anomalies

    anomalies = get_anomalies(STORE_ID)
    spike = [a for a in anomalies if a.anomaly_type == "BILLING_QUEUE_SPIKE"]
    assert spike[0].severity == "CRITICAL"
