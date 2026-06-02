"""Store metrics computation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import get_store_config, load_store_layout
from app.database import (
    db_cursor,
    get_analytics_date,
    parse_timestamp,
    upsert_daily_conversion,
)
from app.models import MetricsResponse

POS_CORRELATION_WINDOW_MINUTES = 5


def get_metrics(store_id: str) -> MetricsResponse:
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
        unique_visitors = len(cursor.fetchall())

        converted = _count_converted_visitors(
            cursor, store_id, analytics_date, billing_zone
        )
        conversion_rate = (
            converted / unique_visitors if unique_visitors > 0 else 0.0
        )

        avg_dwell = _avg_dwell_per_zone(cursor, store_id, analytics_date)
        queue_depth = _current_queue_depth(
            cursor, store_id, billing_zone, analytics_date
        )
        abandonment_rate = _abandonment_rate(cursor, store_id, analytics_date)

        upsert_daily_conversion(cursor, store_id, analytics_date, conversion_rate)

    return MetricsResponse(
        unique_visitors=unique_visitors,
        conversion_rate=conversion_rate,
        avg_dwell_per_zone=avg_dwell,
        current_queue_depth=queue_depth,
        abandonment_rate=abandonment_rate,
    )


def _billing_zone_id(store_id: str) -> str:
    store = get_store_config(store_id)
    if store:
        return store.get("billing_zone_id", "ZONE_BILLING")
    return "ZONE_BILLING"


def _count_converted_visitors(
    cursor, store_id: str, today: str, billing_zone: str
) -> int:
    cursor.execute(
        """
        SELECT DISTINCT e.visitor_id
        FROM events e
        WHERE e.store_id = ? AND e.timestamp LIKE ? AND e.is_staff = 0
          AND e.event_type IN ('ZONE_ENTER', 'ZONE_DWELL')
          AND e.zone_id = ?
        """,
        (store_id, f"{today}%", billing_zone),
    )
    billing_visitors = {row["visitor_id"] for row in cursor.fetchall()}

    cursor.execute(
        """
        SELECT transaction_id, timestamp FROM pos_transactions
        WHERE store_id = ?
        """,
        (store_id,),
    )
    transactions = cursor.fetchall()

    converted: set[str] = set()
    for visitor_id in billing_visitors:
        cursor.execute(
            """
            SELECT timestamp FROM events
            WHERE store_id = ? AND visitor_id = ? AND is_staff = 0
              AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL')
              AND zone_id = ? AND timestamp LIKE ?
            ORDER BY timestamp ASC
            """,
            (store_id, visitor_id, billing_zone, f"{today}%"),
        )
        zone_events = [parse_timestamp(row["timestamp"]) for row in cursor.fetchall()]
        for txn in transactions:
            txn_time = parse_timestamp(txn["timestamp"])
            if txn_time.strftime("%Y-%m-%d") != today:
                continue
            window_start = txn_time - timedelta(minutes=POS_CORRELATION_WINDOW_MINUTES)
            for zone_time in zone_events:
                if window_start <= zone_time <= txn_time:
                    converted.add(visitor_id)
                    break
            if visitor_id in converted:
                break

    return len(converted)


def _avg_dwell_per_zone(cursor, store_id: str, today: str) -> dict[str, float]:
    cursor.execute(
        """
        SELECT zone_id, AVG(dwell_ms) as avg_dwell
        FROM events
        WHERE store_id = ? AND event_type = 'ZONE_DWELL'
          AND timestamp LIKE ? AND is_staff = 0 AND zone_id IS NOT NULL
        GROUP BY zone_id
        """,
        (store_id, f"{today}%"),
    )
    return {
        row["zone_id"]: float(row["avg_dwell"] or 0.0) for row in cursor.fetchall()
    }


def _current_queue_depth(
    cursor, store_id: str, billing_zone: str, analytics_date: str
) -> int:
    """Visitors whose last billing-zone action is enter (not exit), on the analytics date."""
    cursor.execute(
        """
        SELECT visitor_id,
               MAX(CASE WHEN event_type = 'ZONE_ENTER' AND zone_id = ? THEN timestamp END) as last_in,
               MAX(CASE WHEN event_type = 'ZONE_EXIT' AND zone_id = ? THEN timestamp END) as last_out
        FROM events
        WHERE store_id = ? AND is_staff = 0 AND timestamp LIKE ?
        GROUP BY visitor_id
        HAVING last_in IS NOT NULL
           AND (last_out IS NULL OR last_in > last_out)
        """,
        (billing_zone, billing_zone, store_id, f"{analytics_date}%"),
    )
    return len(cursor.fetchall())


def _abandonment_rate(cursor, store_id: str, today: str) -> float:
    cursor.execute(
        """
        SELECT
            SUM(CASE WHEN event_type = 'BILLING_QUEUE_ABANDON' THEN 1 ELSE 0 END) as abandons,
            SUM(CASE WHEN event_type = 'BILLING_QUEUE_JOIN' THEN 1 ELSE 0 END) as joins
        FROM events
        WHERE store_id = ? AND timestamp LIKE ? AND is_staff = 0
        """,
        (store_id, f"{today}%"),
    )
    row = cursor.fetchone()
    joins = row["joins"] or 0
    abandons = row["abandons"] or 0
    return abandons / joins if joins > 0 else 0.0


def get_seven_day_avg_conversion(store_id: str) -> float:
    with db_cursor() as cursor:
        analytics_date = get_analytics_date(cursor, store_id)
    try:
        anchor = datetime.strptime(analytics_date, "%Y-%m-%d").date()
    except ValueError:
        anchor = datetime.now(timezone.utc).date()
    dates = [(anchor - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, 8)]

    with db_cursor() as cursor:
        placeholders = ",".join("?" for _ in dates)
        cursor.execute(
            f"""
            SELECT AVG(conversion_rate) as avg_rate
            FROM daily_conversion_stats
            WHERE store_id = ? AND stat_date IN ({placeholders})
            """,
            (store_id, *dates),
        )
        row = cursor.fetchone()
        if row and row["avg_rate"] is not None:
            return float(row["avg_rate"])

        rates: list[float] = []
        for day in dates:
            metrics = get_metrics_for_date(store_id, day)
            rates.append(metrics.conversion_rate)
        return sum(rates) / len(rates) if rates else 0.0


def get_metrics_for_date(store_id: str, date_str: str) -> MetricsResponse:
    billing_zone = _billing_zone_id(store_id)
    with db_cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT visitor_id FROM events
            WHERE store_id = ? AND event_type = 'ENTRY'
              AND timestamp LIKE ? AND is_staff = 0
            """,
            (store_id, f"{date_str}%"),
        )
        unique_visitors = len(cursor.fetchall())
        converted = _count_converted_visitors(cursor, store_id, date_str, billing_zone)
        conversion_rate = (
            converted / unique_visitors if unique_visitors > 0 else 0.0
        )
        avg_dwell = _avg_dwell_per_zone(cursor, store_id, date_str)
        queue_depth = _current_queue_depth(
            cursor, store_id, billing_zone, date_str
        )
        abandonment_rate = _abandonment_rate(cursor, store_id, date_str)

    return MetricsResponse(
        unique_visitors=unique_visitors,
        conversion_rate=conversion_rate,
        avg_dwell_per_zone=avg_dwell,
        current_queue_depth=queue_depth,
        abandonment_rate=abandonment_rate,
    )


def get_all_store_ids() -> list[str]:
    layout = load_store_layout()
    return [store["store_id"] for store in layout.get("stores", [])]
