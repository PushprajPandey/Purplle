"""Event emission with schema-compliant JSON output."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from app.config import get_events_output_path

INSTANTANEOUS_DWELL_MS = 0
ZONE_DWELL_INTERVAL_SECONDS = 30
BILLING_ABANDON_WINDOW_SECONDS = 300


def build_event(
    *,
    store_id: str,
    camera_id: str,
    visitor_id: str,
    event_type: str,
    timestamp: datetime,
    zone_id: str | None,
    dwell_ms: int,
    is_staff: bool,
    confidence: float,
    sku_zone: str,
    session_seq: int,
    queue_depth: int | None = None,
) -> dict[str, Any]:
    """Build a detection event matching the required schema."""
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": _to_iso(timestamp),
        "zone_id": zone_id,
        "dwell_ms": dwell_ms if dwell_ms is not None else INSTANTANEOUS_DWELL_MS,
        "is_staff": is_staff,
        "confidence": float(confidence),
        "metadata": {
            "queue_depth": queue_depth,
            "sku_zone": sku_zone,
            "session_seq": session_seq,
        },
    }


def emit_event(event: dict[str, Any], output_handle: TextIO) -> dict[str, Any]:
    """Write a single event as JSONL and return the event dict."""
    output_handle.write(json.dumps(event) + "\n")
    output_handle.flush()
    return event


class EventEmitter:
    """Manages event output file and session sequencing."""

    def __init__(
        self,
        store_id: str,
        output_path: Path | None = None,
    ) -> None:
        self.store_id = store_id
        self.output_path = output_path or get_events_output_path()
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._session_seq: dict[str, int] = {}
        self._file: TextIO | None = None

    def open(self, append: bool = False) -> None:
        mode = "a" if append else "w"
        self._file = open(self.output_path, mode, encoding="utf-8")

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None

    def next_session_seq(self, visitor_id: str) -> int:
        current = self._session_seq.get(visitor_id, 0) + 1
        self._session_seq[visitor_id] = current
        return current

    def emit(
        self,
        *,
        camera_id: str,
        visitor_id: str,
        event_type: str,
        timestamp: datetime,
        zone_id: str | None,
        dwell_ms: int = INSTANTANEOUS_DWELL_MS,
        is_staff: bool = False,
        confidence: float = 1.0,
        sku_zone: str = "entry",
        queue_depth: int | None = None,
    ) -> dict[str, Any]:
        if self._file is None:
            raise RuntimeError("EventEmitter must be opened before emitting")

        event = build_event(
            store_id=self.store_id,
            camera_id=camera_id,
            visitor_id=visitor_id,
            event_type=event_type,
            timestamp=timestamp,
            zone_id=zone_id,
            dwell_ms=dwell_ms,
            is_staff=is_staff,
            confidence=confidence,
            sku_zone=sku_zone,
            session_seq=self.next_session_seq(visitor_id),
            queue_depth=queue_depth,
        )
        return emit_event(event, self._file)


def _to_iso(timestamp: datetime) -> str:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
