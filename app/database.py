"""SQLite database layer using stdlib sqlite3 (no ORM)."""

from __future__ import annotations

import csv
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.config import get_db_path, get_pos_transactions_path

CREATE_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    store_id TEXT NOT NULL,
    camera_id TEXT NOT NULL,
    visitor_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    zone_id TEXT,
    dwell_ms INTEGER NOT NULL,
    is_staff INTEGER NOT NULL,
    confidence REAL NOT NULL,
    metadata_json TEXT NOT NULL
);
"""

CREATE_VISITOR_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS visitor_sessions (
    visitor_id TEXT NOT NULL,
    store_id TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    exit_time TEXT,
    zones_visited TEXT NOT NULL DEFAULT '[]',
    converted INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (visitor_id, store_id, entry_time)
);
"""

CREATE_POS_TRANSACTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS pos_transactions (
    store_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    basket_value_inr REAL NOT NULL,
    PRIMARY KEY (store_id, transaction_id)
);
"""

CREATE_DAILY_CONVERSION_TABLE = """
CREATE TABLE IF NOT EXISTS daily_conversion_stats (
    store_id TEXT NOT NULL,
    stat_date TEXT NOT NULL,
    conversion_rate REAL NOT NULL,
    PRIMARY KEY (store_id, stat_date)
);
"""


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db_cursor(db_path: Path | None = None) -> Iterator[sqlite3.Cursor]:
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database(db_path: Path | None = None) -> None:
    with db_cursor(db_path) as cursor:
        cursor.execute(CREATE_EVENTS_TABLE)
        cursor.execute(CREATE_VISITOR_SESSIONS_TABLE)
        cursor.execute(CREATE_POS_TRANSACTIONS_TABLE)
        cursor.execute(CREATE_DAILY_CONVERSION_TABLE)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_store_ts ON events(store_id, timestamp)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_visitor ON events(visitor_id, store_id)"
        )


def load_pos_transactions(db_path: Path | None = None) -> int:
    pos_path = get_pos_transactions_path()
    if not pos_path.exists():
        return 0

    inserted = 0
    with db_cursor(db_path) as cursor:
        with open(pos_path, encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO pos_transactions
                    (store_id, transaction_id, timestamp, basket_value_inr)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        row["store_id"],
                        row["transaction_id"],
                        row["timestamp"],
                        float(row["basket_value_inr"]),
                    ),
                )
                if cursor.rowcount > 0:
                    inserted += 1
    return inserted


def insert_event(cursor: sqlite3.Cursor, event: dict[str, Any]) -> bool:
    cursor.execute(
        """
        INSERT OR IGNORE INTO events
        (event_id, store_id, camera_id, visitor_id, event_type, timestamp,
         zone_id, dwell_ms, is_staff, confidence, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event["event_id"],
            event["store_id"],
            event["camera_id"],
            event["visitor_id"],
            event["event_type"],
            event["timestamp"],
            event.get("zone_id"),
            event["dwell_ms"],
            1 if event["is_staff"] else 0,
            event["confidence"],
            json.dumps(event["metadata"]),
        ),
    )
    return cursor.rowcount > 0


def handle_entry_session(cursor: sqlite3.Cursor, event: dict[str, Any]) -> None:
    cursor.execute(
        """
        INSERT OR IGNORE INTO visitor_sessions
        (visitor_id, store_id, entry_time, exit_time, zones_visited, converted)
        VALUES (?, ?, ?, NULL, '[]', 0)
        """,
        (event["visitor_id"], event["store_id"], event["timestamp"]),
    )


def handle_exit_session(cursor: sqlite3.Cursor, event: dict[str, Any]) -> None:
    cursor.execute(
        """
        UPDATE visitor_sessions
        SET exit_time = ?
        WHERE rowid = (
            SELECT rowid FROM visitor_sessions
            WHERE visitor_id = ? AND store_id = ? AND exit_time IS NULL
            ORDER BY entry_time DESC
            LIMIT 1
        )
        """,
        (event["timestamp"], event["visitor_id"], event["store_id"]),
    )


def handle_zone_enter_session(cursor: sqlite3.Cursor, event: dict[str, Any]) -> None:
    zone_id = event.get("zone_id")
    if not zone_id:
        return
    cursor.execute(
        """
        SELECT rowid, zones_visited FROM visitor_sessions
        WHERE visitor_id = ? AND store_id = ? AND exit_time IS NULL
        ORDER BY entry_time DESC LIMIT 1
        """,
        (event["visitor_id"], event["store_id"]),
    )
    row = cursor.fetchone()
    if not row:
        return
    zones = json.loads(row["zones_visited"])
    if zone_id not in zones:
        zones.append(zone_id)
        cursor.execute(
            "UPDATE visitor_sessions SET zones_visited = ? WHERE rowid = ?",
            (json.dumps(zones), row["rowid"]),
        )


def event_exists(cursor: sqlite3.Cursor, event_id: str) -> bool:
    cursor.execute("SELECT 1 FROM events WHERE event_id = ?", (event_id,))
    return cursor.fetchone() is not None


def utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def get_analytics_date(cursor: sqlite3.Cursor, store_id: str) -> str:
    """Use the calendar date of the most recent events (CCTV clip day), not wall-clock today."""
    cursor.execute(
        """
        SELECT MAX(substr(timestamp, 1, 10)) as latest_date
        FROM events WHERE store_id = ? AND is_staff = 0
        """,
        (store_id,),
    )
    row = cursor.fetchone()
    latest = row["latest_date"] if row else None
    return latest or utc_today()


def parse_timestamp(ts: str) -> datetime:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


def upsert_daily_conversion(
    cursor: sqlite3.Cursor, store_id: str, stat_date: str, conversion_rate: float
) -> None:
    cursor.execute(
        """
        INSERT INTO daily_conversion_stats (store_id, stat_date, conversion_rate)
        VALUES (?, ?, ?)
        ON CONFLICT(store_id, stat_date) DO UPDATE SET conversion_rate = excluded.conversion_rate
        """,
        (store_id, stat_date, conversion_rate),
    )
