"""Pydantic models for Store Intelligence API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

EventType = Literal[
    "ENTRY",
    "EXIT",
    "ZONE_ENTER",
    "ZONE_EXIT",
    "ZONE_DWELL",
    "BILLING_QUEUE_JOIN",
    "BILLING_QUEUE_ABANDON",
    "REENTRY",
]


class EventMetadata(BaseModel):
    queue_depth: int | None = None
    sku_zone: str
    session_seq: int


class EventIn(BaseModel):
    event_id: str
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: EventType
    timestamp: str
    zone_id: str | None = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: EventMetadata

    @field_validator("visitor_id")
    @classmethod
    def validate_visitor_id(cls, value: str) -> str:
        if not value.startswith("VIS_"):
            raise ValueError("visitor_id must start with VIS_")
        return value


class IngestRequest(BaseModel):
    events: list[EventIn] = Field(max_length=500)


class ValidationErrorDetail(BaseModel):
    event_id: str | None
    error: str


class IngestResponse(BaseModel):
    total_received: int
    inserted: int
    skipped_duplicates: int
    validation_errors: list[ValidationErrorDetail]


class MetricsResponse(BaseModel):
    unique_visitors: int
    conversion_rate: float
    avg_dwell_per_zone: dict[str, float]
    current_queue_depth: int
    abandonment_rate: float


class FunnelStage(BaseModel):
    count: int
    drop_off_percent: float


class FunnelResponse(BaseModel):
    total_entries: FunnelStage
    zone_visitors: FunnelStage
    billing_visitors: FunnelStage
    purchasers: FunnelStage


class HeatmapZone(BaseModel):
    zone_id: str
    visit_frequency_normalised: float
    avg_dwell_ms: float


class HeatmapResponse(BaseModel):
    zones: list[HeatmapZone]
    data_confidence: bool


class Anomaly(BaseModel):
    anomaly_type: str
    severity: Literal["INFO", "WARN", "CRITICAL"]
    description: str
    suggested_action: str
    detected_at: str


class HealthResponse(BaseModel):
    status: Literal["healthy", "degraded"]
    database_connected: bool
    last_event_per_store: dict[str, str | None]
    stale_feed: dict[str, bool]


class ErrorResponse(BaseModel):
    error: str
    trace_id: str
    message: str
