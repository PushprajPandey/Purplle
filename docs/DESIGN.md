# Store Intelligence — Design Document

## System Architecture Overview

Store Intelligence is an end-to-end retail analytics platform that transforms raw CCTV footage into actionable store metrics. The journey begins when MP4 clips from entry and floor cameras are fed into a Python detection pipeline. YOLOv8 identifies people in each frame at 15 frames per second; ByteTrack assigns persistent track IDs across frames. A trajectory-based Re-ID layer merges exits and re-entries so returning shoppers are not double-counted. The pipeline emits structured JSON events (entry, zone dwell, billing queue, abandonment) to a JSONL file. A bash orchestrator can batch-ingest those events into a FastAPI service backed by SQLite. The API correlates visitor sessions with POS transactions to compute conversion rate—the north star metric—along with funnel stages, heatmaps, and live anomalies. A light-theme web dashboard at `/` and an optional Rich terminal dashboard poll the API every 2–3 seconds for operations visibility.

## Component Responsibilities

**Detection (`pipeline/detect.py`)** loads video via OpenCV, selects YOLOv8n for entry cameras and YOLOv8m for floor cameras based on `store_layout.json`, and runs person-only inference at 15fps by skipping frames proportional to source FPS. Detections below 0.5 confidence are still forwarded to tracking with their true confidence values.

**Tracking (`pipeline/tracker.py`)** implements IoU-based ByteTrack association, maintaining active tracks with centroid histories. Group entry (three or more threshold crossings within two seconds) always produces separate ENTRY events per bounding box.

**Re-ID** stores exited visitors’ last ten centroid positions. Re-entries within thirty seconds from a similar entry region are matched via cosine similarity and emit REENTRY instead of ENTRY.

**Event emission (`pipeline/emit.py`)** builds schema-compliant JSON with UUID event IDs, ISO-8601 timestamps, session sequence ordinals, and writes one JSON object per line to `events_output.jsonl`.

**API (`app/`)** validates ingest payloads with Pydantic, deduplicates by `event_id` using `INSERT OR IGNORE`, maintains `visitor_sessions`, loads POS data on startup, and exposes metrics, funnel, heatmap, anomaly, and health endpoints.

**Anomaly detection (`app/anomalies.py`)** evaluates billing queue depth, conversion rate vs seven-day average, and dead zones with no ZONE_ENTER activity for thirty minutes during open hours.

## Data Flow Diagram (Text)

```
CCTV MP4 → detect.py (YOLOv8 @ 15fps)
         → tracker.py (ByteTrack + Re-ID + staff HSV + zones)
         → emit.py → events_output.jsonl
         → run.sh POST /events/ingest (batches of 500)
         → SQLite (events, visitor_sessions, pos_transactions)
         → GET /metrics, /funnel, /heatmap, /anomalies
         → frontend/ (web UI at /) or dashboard/live.py (Rich table)
```

## AI-Assisted Decisions

1. **Trajectory Re-ID vs embedding model:** An LLM suggested using a lightweight OSNet embedding for cross-frame identity. We chose centroid-trajectory cosine matching instead because it requires no extra model download, keeps the pipeline runnable on CPU-only assessment machines, and satisfies the spec’s “bounding-box-trajectory-based Re-ID” requirement with interpretable thresholds.

2. **Aggregated dwell events vs per-frame zone state:** The AI recommended emitting ZONE_DWELL only on zone exit with total duration. We disagreed and emit ZONE_DWELL every thirty seconds while a visitor remains in-zone so the API can compute live average dwell without reconstructing full trajectories from raw events.

3. **PostgreSQL for analytics:** The AI initially proposed PostgreSQL with Timescale for time-series events. We overrode this for SQLite with explicit `INSERT OR IGNORE` idempotency because the challenge scope is single-store, single-container, and write volume is ingest-batched—not streaming millions of events per second.

## Known Limitations

- **Re-ID accuracy** degrades when multiple visitors re-enter simultaneously from the same door; trajectory-only matching can confuse similar paths.
- **Staff detection** assumes a quiet calibration window in the first two minutes; heavy early foot traffic can mis-calibrate the uniform colour profile.
- **POS correlation** uses a five-minute billing-zone window and cannot link anonymous visitors to specific basket SKUs.
- **YOLO on CPU** is slow on long clips; the pipeline continues through empty periods but real-time processing is not guaranteed without GPU.
- **Graceful degradation:** API returns partial ingest success for bad events; health reports `degraded` when feeds are stale; metrics return `0.0` conversion when no visitors exist rather than errors.
