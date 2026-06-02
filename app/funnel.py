"""Session-based conversion funnel."""

from __future__ import annotations

from app.config import get_store_config
from app.database import db_cursor, get_analytics_date, parse_timestamp
from app.metrics import POS_CORRELATION_WINDOW_MINUTES
from app.models import FunnelResponse, FunnelStage

from datetime import timedelta


def get_funnel(store_id: str) -> FunnelResponse:
    billing_zone = _billing_zone_id(store_id)

    with db_cursor() as cursor:
        analytics_date = get_analytics_date(cursor, store_id)
        cursor.execute(
            """
            SELECT DISTINCT visitor_id FROM events
            WHERE store_id = ? AND event_type = 'ENTRY'
              AND timestamp LIKE ? AND is_staff = 0
            """,
            (store_id, f"{analytics_date}%"),
        )
        total_entries = len(cursor.fetchall())

        cursor.execute(
            """
            SELECT DISTINCT visitor_id FROM events
            WHERE store_id = ? AND event_type = 'ZONE_ENTER'
              AND timestamp LIKE ? AND is_staff = 0
            """,
            (store_id, f"{analytics_date}%"),
        )
        zone_visitors = len(cursor.fetchall())

        cursor.execute(
            """
            SELECT DISTINCT visitor_id FROM events
            WHERE store_id = ? AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL')
              AND zone_id = ? AND timestamp LIKE ? AND is_staff = 0
            """,
            (store_id, billing_zone, f"{analytics_date}%"),
        )
        billing_visitors = len(cursor.fetchall())

        purchasers = _count_purchasers(cursor, store_id, analytics_date, billing_zone)

    return FunnelResponse(
        total_entries=_stage(total_entries, None),
        zone_visitors=_stage(zone_visitors, total_entries),
        billing_visitors=_stage(billing_visitors, zone_visitors),
        purchasers=_stage(purchasers, billing_visitors),
    )


def _billing_zone_id(store_id: str) -> str:
    store = get_store_config(store_id)
    if store:
        return store.get("billing_zone_id", "ZONE_BILLING")
    return "ZONE_BILLING"


def _stage(count: int, previous: int | None) -> FunnelStage:
    if previous is None or previous == 0:
        drop_off = 0.0
    else:
        drop_off = max(0.0, (previous - count) / previous * 100.0)
    return FunnelStage(count=count, drop_off_percent=round(drop_off, 2))


def _count_purchasers(cursor, store_id: str, today: str, billing_zone: str) -> int:
    cursor.execute(
        """
        SELECT DISTINCT visitor_id FROM events
        WHERE store_id = ? AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL')
          AND zone_id = ? AND timestamp LIKE ? AND is_staff = 0
        """,
        (store_id, billing_zone, f"{today}%"),
    )
    billing_visitors = [row["visitor_id"] for row in cursor.fetchall()]

    cursor.execute(
        "SELECT timestamp FROM pos_transactions WHERE store_id = ?",
        (store_id,),
    )
    transactions = cursor.fetchall()

    purchasers: set[str] = set()
    for visitor_id in billing_visitors:
        cursor.execute(
            """
            SELECT timestamp FROM events
            WHERE store_id = ? AND visitor_id = ? AND is_staff = 0
              AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL')
              AND zone_id = ? AND timestamp LIKE ?
            """,
            (store_id, visitor_id, billing_zone, f"{today}%"),
        )
        zone_times = [parse_timestamp(r["timestamp"]) for r in cursor.fetchall()]
        for txn in transactions:
            txn_time = parse_timestamp(txn["timestamp"])
            if txn_time.strftime("%Y-%m-%d") != today:
                continue
            window_start = txn_time - timedelta(minutes=POS_CORRELATION_WINDOW_MINUTES)
            for zone_time in zone_times:
                if window_start <= zone_time <= txn_time:
                    purchasers.add(visitor_id)
                    break
            if visitor_id in purchasers:
                break

    return len(purchasers)
