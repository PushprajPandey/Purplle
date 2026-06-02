"""Load bundled demo events when the database is empty (cloud cold start)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from app.config import get_data_dir
from app.database import db_cursor
from app.ingestion import ingest_events_raw

logger = logging.getLogger("store_intelligence")


def _seed_path() -> Path | None:
    data_dir = get_data_dir()
    for name in ("events_seed.jsonl", "events_output.jsonl"):
        candidate = data_dir / name
        if candidate.is_file():
            return candidate
    return None


def seed_demo_events_if_empty() -> None:
    if os.environ.get("SEED_DEMO_DATA", "true").lower() in ("0", "false", "no"):
        return
    with db_cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS cnt FROM events")
        if (cursor.fetchone()["cnt"] or 0) > 0:
            return

    path = _seed_path()
    if path is None:
        logger.warning("No events seed file found; dashboard will be empty until ingest")
        return

    events: list[dict] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                events.append(json.loads(line))

    if not events:
        return

    result = ingest_events_raw(events)
    logger.info(
        "Seeded demo events from %s: inserted=%s skipped=%s",
        path.name,
        result.inserted,
        result.skipped_duplicates,
    )
