"""FastAPI application entry point."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Callable

from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.anomalies import get_anomalies, get_heatmap
from app.config import get_cors_origins, get_store_config, load_store_layout
from app.errors import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.context import trace_id_var
from app.seed import seed_demo_events_if_empty
from app.database import init_database, load_pos_transactions
from app.funnel import get_funnel
from app.health import get_health
from app.metrics import get_metrics


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", trace_id_var.get()),
            "store_id": getattr(record, "store_id", None),
            "endpoint": getattr(record, "endpoint", None),
            "latency_ms": getattr(record, "latency_ms", None),
            "event_count": getattr(record, "event_count", None),
            "status_code": getattr(record, "status_code", None),
        }
        return json.dumps(payload)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


configure_logging()
logger = logging.getLogger("store_intelligence")

app = FastAPI(title="Store Intelligence API", version="1.0.0")
STORE_LAYOUT: dict = {}

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.on_event("startup")
def on_startup() -> None:
    global STORE_LAYOUT
    init_database()
    load_pos_transactions()
    seed_demo_events_if_empty()
    STORE_LAYOUT = load_store_layout()
    logger.info("Store Intelligence API started")


def _ensure_store(store_id: str) -> None:
    if get_store_config(store_id) is None:
        raise StarletteHTTPException(
            status_code=404,
            detail=f"Unknown store_id: {store_id}",
        )


@app.middleware("http")
async def trace_and_error_middleware(request: Request, call_next: Callable) -> Response:
    trace_id = str(uuid.uuid4())
    trace_id_var.set(trace_id)
    start = time.perf_counter()
    store_id = request.path_params.get("store_id")

    response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id

    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    event_count = getattr(request.state, "event_count", None)

    logger.info(
        "request completed",
        extra={
            "trace_id": trace_id,
            "store_id": store_id,
            "endpoint": request.url.path,
            "latency_ms": latency_ms,
            "event_count": event_count,
            "status_code": response.status_code,
        },
    )
    return response


@app.get("/")
def dashboard_ui():
    """Web dashboard (Part E — bonus live UI)."""
    index = FRONTEND_DIR / "index.html"
    if index.is_file():
        return FileResponse(index)
    return {
        "service": "store-intelligence",
        "docs": "/docs",
        "health": "/health",
        "dashboard": "frontend not found — use /docs",
    }


@app.get("/api")
def api_info() -> dict:
    return {
        "service": "store-intelligence",
        "docs": "/docs",
        "health": "/health",
        "dashboard": "/",
        "example_metrics": "/stores/STORE_BLR_002/metrics",
    }


@app.post("/events/ingest")
async def post_events_ingest(request: Request) -> dict:
    body = await request.json()
    raw_events = body.get("events", []) if isinstance(body, dict) else []
    request.state.event_count = len(raw_events)
    from app.ingestion import ingest_events_raw

    try:
        return ingest_events_raw(raw_events).model_dump()
    except Exception as exc:
        logger.exception("ingest failed: %s", exc)
        return {
            "total_received": len(raw_events),
            "inserted": 0,
            "skipped_duplicates": 0,
            "validation_errors": [
                {"event_id": None, "error": f"ingest_error: {exc}"}
            ],
        }


@app.get("/stores/{store_id}/metrics")
def get_store_metrics(store_id: str) -> dict:
    _ensure_store(store_id)
    return get_metrics(store_id).model_dump()


@app.get("/stores/{store_id}/funnel")
def get_store_funnel(store_id: str) -> dict:
    _ensure_store(store_id)
    return get_funnel(store_id).model_dump()


@app.get("/stores/{store_id}/heatmap")
def get_store_heatmap(store_id: str) -> dict:
    _ensure_store(store_id)
    return get_heatmap(store_id).model_dump()


@app.get("/stores/{store_id}/anomalies")
def get_store_anomalies(store_id: str) -> list[dict]:
    _ensure_store(store_id)
    return [a.model_dump() for a in get_anomalies(store_id)]


@app.get("/health")
def health_check() -> dict:
    return get_health().model_dump()
