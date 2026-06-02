"""Event ingestion endpoint logic."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from app.database import (
    db_cursor,
    event_exists,
    handle_entry_session,
    handle_exit_session,
    handle_zone_enter_session,
    insert_event,
)
from app.models import EventIn, IngestRequest, IngestResponse, ValidationErrorDetail

logger = logging.getLogger(__name__)


def ingest_events(payload: IngestRequest) -> IngestResponse:
    total_received = len(payload.events)
    inserted = 0
    skipped_duplicates = 0
    validation_errors: list[ValidationErrorDetail] = []

    with db_cursor() as cursor:
        for raw_event in payload.events:
            try:
                event = EventIn.model_validate(raw_event.model_dump())
            except ValidationError as exc:
                event_id = getattr(raw_event, "event_id", None)
                validation_errors.append(
                    ValidationErrorDetail(event_id=event_id, error=str(exc))
                )
                continue

            event_dict = event.model_dump()
            if event_exists(cursor, event.event_id):
                skipped_duplicates += 1
                continue

            if insert_event(cursor, event_dict):
                inserted += 1
                try:
                    _update_sessions(cursor, event_dict)
                except Exception as exc:
                    logger.warning(
                        "session update skipped for %s: %s",
                        event_dict.get("event_id"),
                        exc,
                    )

    return IngestResponse(
        total_received=total_received,
        inserted=inserted,
        skipped_duplicates=skipped_duplicates,
        validation_errors=validation_errors,
    )


def ingest_events_raw(events: list[dict[str, Any]]) -> IngestResponse:
    """Ingest from raw dicts (used by tests and batch scripts)."""
    parsed: list[EventIn] = []
    validation_errors: list[ValidationErrorDetail] = []
    for raw in events:
        try:
            parsed.append(EventIn.model_validate(raw))
        except ValidationError as exc:
            validation_errors.append(
                ValidationErrorDetail(
                    event_id=raw.get("event_id"), error=str(exc)
                )
            )

    request = IngestRequest(events=parsed)
    response = ingest_events(request)
    response.validation_errors.extend(validation_errors)
    response.total_received = len(events)
    return response


def _update_sessions(cursor, event: dict[str, Any]) -> None:
    event_type = event["event_type"]
    if event_type == "ENTRY":
        handle_entry_session(cursor, event)
    elif event_type == "EXIT":
        handle_exit_session(cursor, event)
    elif event_type in ("ZONE_ENTER", "ZONE_DWELL"):
        handle_zone_enter_session(cursor, event)
