"""Health check endpoint logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import load_store_layout
from app.database import db_cursor, parse_timestamp
from app.models import HealthResponse

STALE_FEED_MINUTES = 10


def get_health() -> HealthResponse:
    database_connected = True
    last_event_per_store: dict[str, str | None] = {}
    stale_feed: dict[str, bool] = {}

    try:
        layout = load_store_layout()
        store_ids = [s["store_id"] for s in layout.get("stores", [])]
    except Exception:
        database_connected = False
        return HealthResponse(
            status="degraded",
            database_connected=False,
            last_event_per_store={},
            stale_feed={},
        )

    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(minutes=STALE_FEED_MINUTES)

    try:
        with db_cursor() as cursor:
            for store_id in store_ids:
                cursor.execute(
                    """
                    SELECT MAX(timestamp) as last_ts FROM events
                    WHERE store_id = ?
                    """,
                    (store_id,),
                )
                row = cursor.fetchone()
                last_ts = row["last_ts"] if row else None
                last_event_per_store[store_id] = last_ts

                if last_ts is None:
                    stale_feed[store_id] = True
                else:
                    last_dt = parse_timestamp(last_ts)
                    stale_feed[store_id] = last_dt < stale_cutoff
    except Exception:
        database_connected = False

    status = "healthy" if database_connected and not any(stale_feed.values()) else "degraded"

    return HealthResponse(
        status=status,
        database_connected=database_connected,
        last_event_per_store=last_event_per_store,
        stale_feed=stale_feed,
    )
