"""Heatmap and anomaly detection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import get_store_config
from app.database import db_cursor, get_analytics_date, parse_timestamp
from app.metrics import get_metrics, get_seven_day_avg_conversion
from app.models import Anomaly, HeatmapResponse, HeatmapZone

QUEUE_SPIKE_WARN_THRESHOLD = 5
QUEUE_SPIKE_CRITICAL_THRESHOLD = 10
CONVERSION_DROP_WARN_PERCENT = 20
CONVERSION_DROP_CRITICAL_PERCENT = 40
DEAD_ZONE_MINUTES = 30
SESSION_CONFIDENCE_MIN = 20


def get_heatmap(store_id: str) -> HeatmapResponse:
    store = get_store_config(store_id)
    zones_config = store.get("zones", []) if store else []

    with db_cursor() as cursor:
        analytics_date = get_analytics_date(cursor, store_id)
        cursor.execute(
            """
            SELECT zone_id, COUNT(*) as visits
            FROM events
            WHERE store_id = ? AND event_type = 'ZONE_ENTER'
              AND timestamp LIKE ? AND is_staff = 0 AND zone_id IS NOT NULL
            GROUP BY zone_id
            """,
            (store_id, f"{analytics_date}%"),
        )
        visit_counts = {row["zone_id"]: row["visits"] for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT zone_id, AVG(dwell_ms) as avg_dwell
            FROM events
            WHERE store_id = ? AND event_type = 'ZONE_DWELL'
              AND timestamp LIKE ? AND is_staff = 0 AND zone_id IS NOT NULL
            GROUP BY zone_id
            """,
            (store_id, f"{analytics_date}%"),
        )
        dwell_avgs = {
            row["zone_id"]: float(row["avg_dwell"] or 0.0)
            for row in cursor.fetchall()
        }

        cursor.execute(
            """
            SELECT COUNT(DISTINCT visitor_id) as sessions
            FROM events
            WHERE store_id = ? AND event_type = 'ENTRY'
              AND timestamp LIKE ? AND is_staff = 0
            """,
            (store_id, f"{analytics_date}%"),
        )
        session_count = cursor.fetchone()["sessions"] or 0

    max_visits = max(visit_counts.values()) if visit_counts else 0
    zones: list[HeatmapZone] = []
    for zone in zones_config:
        zone_id = zone["zone_id"]
        visits = visit_counts.get(zone_id, 0)
        normalized = (visits / max_visits * 100.0) if max_visits > 0 else 0.0
        zones.append(
            HeatmapZone(
                zone_id=zone_id,
                visit_frequency_normalised=round(normalized, 2),
                avg_dwell_ms=round(dwell_avgs.get(zone_id, 0.0), 2),
            )
        )

    return HeatmapResponse(
        zones=zones,
        data_confidence=session_count >= SESSION_CONFIDENCE_MIN,
    )


def get_anomalies(store_id: str) -> list[Anomaly]:
    now = datetime.now(timezone.utc)
    detected_at = now.isoformat().replace("+00:00", "Z")
    anomalies: list[Anomaly] = []

    try:
        metrics = get_metrics(store_id)
        anomalies.extend(
            _billing_queue_spike(metrics.current_queue_depth, detected_at)
        )
        anomalies.extend(
            _conversion_drop(store_id, metrics.conversion_rate, detected_at)
        )
        anomalies.extend(_dead_zones(store_id, detected_at))
    except Exception:
        return []

    return anomalies


def _billing_queue_spike(queue_depth: int, detected_at: str) -> list[Anomaly]:
    if queue_depth <= QUEUE_SPIKE_WARN_THRESHOLD:
        return []
    severity = (
        "CRITICAL"
        if queue_depth > QUEUE_SPIKE_CRITICAL_THRESHOLD
        else "WARN"
    )
    return [
        Anomaly(
            anomaly_type="BILLING_QUEUE_SPIKE",
            severity=severity,
            description=(
                f"Billing queue depth is {queue_depth}, exceeding threshold of "
                f"{QUEUE_SPIKE_WARN_THRESHOLD}."
            ),
            suggested_action="Deploy additional billing staff immediately.",
            detected_at=detected_at,
        )
    ]


def _conversion_drop(
    store_id: str, today_rate: float, detected_at: str
) -> list[Anomaly]:
    avg_rate = get_seven_day_avg_conversion(store_id)
    if avg_rate <= 0:
        return []

    drop_percent = (avg_rate - today_rate) / avg_rate * 100.0
    if drop_percent <= CONVERSION_DROP_WARN_PERCENT:
        return []

    severity = (
        "CRITICAL"
        if drop_percent > CONVERSION_DROP_CRITICAL_PERCENT
        else "WARN"
    )
    return [
        Anomaly(
            anomaly_type="CONVERSION_DROP",
            severity=severity,
            description=(
                f"Today's conversion rate ({today_rate:.2%}) is {drop_percent:.1f}% "
                f"below the 7-day average ({avg_rate:.2%})."
            ),
            suggested_action=(
                "Review floor staff placement and promotional signage."
            ),
            detected_at=detected_at,
        )
    ]


def _dead_zones(store_id: str, detected_at: str) -> list[Anomaly]:
    store = get_store_config(store_id)
    if not store:
        return []

    with db_cursor() as cursor:
        cursor.execute(
            "SELECT MAX(timestamp) as max_ts FROM events WHERE store_id = ?",
            (store_id,),
        )
        row = cursor.fetchone()
        if not row or not row["max_ts"]:
            return []
        reference = parse_timestamp(row["max_ts"])

    if not _is_store_open(store, reference):
        return []

    cutoff = reference - timedelta(minutes=DEAD_ZONE_MINUTES)
    cutoff_str = cutoff.isoformat().replace("+00:00", "Z")
    anomalies: list[Anomaly] = []

    with db_cursor() as cursor:
        for zone in store.get("zones", []):
            zone_id = zone["zone_id"]
            cursor.execute(
                """
                SELECT COUNT(*) as cnt FROM events
                WHERE store_id = ? AND zone_id = ? AND event_type = 'ZONE_ENTER'
                  AND timestamp >= ? AND is_staff = 0
                """,
                (store_id, zone_id, cutoff_str),
            )
            if cursor.fetchone()["cnt"] == 0:
                anomalies.append(
                    Anomaly(
                        anomaly_type="DEAD_ZONE",
                        severity="INFO",
                        description=(
                            f"No ZONE_ENTER activity in {zone_id} for the last "
                            f"{DEAD_ZONE_MINUTES} minutes."
                        ),
                        suggested_action=(
                            f"Check camera feed and consider repositioning display "
                            f"for zone {zone_id}."
                        ),
                        detected_at=detected_at,
                    )
                )

    return anomalies


def _is_store_open(store: dict, now: datetime) -> bool:
    open_hours = store.get("open_hours", {})
    start = open_hours.get("start", "00:00")
    end = open_hours.get("end", "23:59")
    current_time = now.strftime("%H:%M")
    return start <= current_time <= end
