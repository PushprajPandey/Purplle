"""Test helper utilities."""

from __future__ import annotations


def make_event(
    *,
    event_id: str,
    visitor_id: str = "VIS_abc123",
    event_type: str = "ENTRY",
    timestamp: str = "2026-04-10T12:00:00Z",
    is_staff: bool = False,
    confidence: float = 0.9,
    zone_id: str | None = "ZONE_ENTRY",
    dwell_ms: int = 0,
    queue_depth: int | None = None,
    session_seq: int = 1,
    sku_zone: str = "entry",
) -> dict:
    return {
        "event_id": event_id,
        "store_id": "STORE_BLR_002",
        "camera_id": "CAM_1",
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": confidence,
        "metadata": {
            "queue_depth": queue_depth,
            "sku_zone": sku_zone,
            "session_seq": session_seq,
        },
    }
