# Store Intelligence

> End-to-end retail analytics — raw CCTV footage → person detection → structured visitor events → live conversion metrics API.

**North star metric:** Offline store conversion rate = unique visitors who purchased ÷ total unique visitors.

---

## Table of Contents

- [Quick Start](#quick-start)
- [What This Builds](#what-this-builds)
- [Running the Detection Pipeline](#running-the-detection-pipeline)
- [Running Without Docker](#running-without-docker)
- [Live Dashboard](#live-dashboard)
- [API Reference](#api-reference)
- [Event Flow](#event-flow)
- [Configuration](#configuration)
- [Tests](#tests)
- [Deployment](#deployment)
- [Project Layout](#project-layout)

---

## Quick Start

Five commands from zero to running API with live dashboard:

```bash
git clone https://github.com/YOUR_USER/store-intelligence.git
cd store-intelligence
pip install -r requirements.txt
docker compose up --build
```

Open **[http://localhost:8000/](http://localhost:8000/)** — the web dashboard auto-refreshes every 3 seconds.

Then feed it footage (see [Running the Detection Pipeline](#running-the-detection-pipeline) below).

> **Hosted demo:** If running on Render free tier, allow ~30 seconds on first load for the instance to wake from idle.

---

## What This Builds

```
📹 CCTV Footage (MP4)
        │
        ▼
🔍 Detection Layer         YOLOv8 + ByteTrack + Re-ID
   pipeline/detect.py  →   events_output.jsonl
        │
        ▼
⚡ Event Ingest             POST /events/ingest (batches of 500)
   FastAPI + SQLite     →   visitor_sessions, pos_transactions
        │
        ▼
🧠 Intelligence API         /metrics  /funnel  /heatmap  /anomalies
        │
        ▼
📊 Live Dashboard           Web UI at /  ·  Rich terminal via dashboard/live.py
```

| Part | What's Built | Points |
|------|-------------|--------|
| A — Detection Pipeline | YOLOv8 + ByteTrack, Re-ID, staff exclusion, group/re-entry handling | 30 |
| B — Intelligence API | 6 endpoints, funnel, heatmap, anomaly detection | 35 |
| C — Production Readiness | Docker, structured logs, idempotent ingest, tests | 20 |
| D — AI Engineering | DESIGN.md, CHOICES.md, prompt blocks in tests | 15 |
| E — Live Dashboard (bonus) | Web UI + terminal dashboard, real-time updates | +10 |

---

## Running the Detection Pipeline

### All 5 cameras at once (recommended)

```powershell
.\pipeline\run_all.ps1 -Overwrite
```

This script processes every MP4 in `DATA_DIR`, appends events to `pipeline/events_output.jsonl`, and POSTs them to the API automatically.

### Manual per-camera (Windows PowerShell)

```powershell
$cam = "D:\purplle\CCTV Footage-20260529T160731Z-3-00144614ea\CCTV Footage"

# CAM 1 — entry/exit threshold (use --overwrite to start fresh)
python pipeline/detect.py --video "$cam\CAM 1.mp4" --store_id STORE_BLR_002 --overwrite

# CAM 2, 3, 4, 5 — floor zones and billing (append automatically)
python pipeline/detect.py --video "$cam\CAM 2.mp4" --store_id STORE_BLR_002
python pipeline/detect.py --video "$cam\CAM 3.mp4" --store_id STORE_BLR_002
python pipeline/detect.py --video "$cam\CAM 4.mp4" --store_id STORE_BLR_002
python pipeline/detect.py --video "$cam\CAM 5.mp4" --store_id STORE_BLR_002

# Ingest all emitted events into the API
python pipeline/ingest_events.py
```

Each `detect.py` run prints a JSON status line on completion:

```json
{"status": "complete", "events_output": "pipeline/events_output.jsonl", "events_written": 118}
```

> **Note on CAM 4:** The Brigade Road footage for CAM 4 contains no human detections. The pipeline exits cleanly with `events_written: 0` — this is expected behaviour, not a bug.

### Disabling staff colour detection

If you want all detected persons treated as customers (useful for testing):

```powershell
$env:STAFF_DETECTION_ENABLED = "false"
python pipeline/detect.py --video "$cam\CAM 1.mp4" --store_id STORE_BLR_002
```

### Where events go

All events are written to `pipeline/events_output.jsonl` (one JSON object per line). The ingest script reads this file and batches events to `POST /events/ingest` in groups of 500. Events are deduplicated by `event_id` — running the ingest script twice is safe.

---

## Running Without Docker

```bash
cd store-intelligence
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The API auto-seeds demo data from `deploy/data/events_seed.jsonl` on first start when the database is empty, so the dashboard is not blank out of the box.

---

## Live Dashboard

### Web UI (Part E — bonus)

Open **[http://localhost:8000/](http://localhost:8000/)** after `docker compose up`.

The dashboard shows:

- **KPI cards** — unique visitors, conversion rate %, queue depth, abandonment rate
- **Conversion funnel** — Entry → Zone Visit → Billing Queue → Purchase with drop-off %
- **Zone heatmap** — visit frequency normalised 0–100 with average dwell per zone
- **Active anomalies** — severity badges (INFO / WARN / CRITICAL) with suggested actions
- **Health banner** — stale feed warning if any store has no events in the last 10 minutes

Refreshes every 3 seconds. No build step — plain HTML/CSS/JS served at `/static`.

### Terminal dashboard (optional)

```bash
python dashboard/live.py
```

Rich table refreshing every 2 seconds showing store ID, unique visitors, conversion %, queue depth, and active anomaly count. Rows turn yellow on WARN anomalies, red on CRITICAL.

| Interface | URL / Command |
|-----------|--------------|
| Web dashboard | [http://localhost:8000/](http://localhost:8000/) |
| Swagger / interactive docs | [http://localhost:8000/docs](http://localhost:8000/docs) |
| API metadata | [http://localhost:8000/api](http://localhost:8000/api) |
| Terminal dashboard | `python dashboard/live.py` |

---

## API Reference

All responses are JSON. Errors return `{"error", "message", "trace_id", "status_code"}`. Every response carries an `X-Trace-Id` header.

### `POST /events/ingest`

Accepts a JSON array of up to 500 events. Validates each against the Pydantic schema, deduplicates by `event_id`, and stores valid events. Returns partial success — malformed events are reported per-event without rejecting the rest of the batch.

```bash
curl -X POST http://localhost:8000/events/ingest \
  -H "Content-Type: application/json" \
  -d @pipeline/events_output.jsonl
```

Response:

```json
{
  "total_received": 118,
  "inserted": 115,
  "skipped_duplicates": 3,
  "validation_errors": []
}
```

### `GET /stores/{store_id}/metrics`

Returns today's customer metrics for a store. Staff events (`is_staff: true`) are always excluded.

```bash
curl http://localhost:8000/stores/STORE_BLR_002/metrics
```

```json
{
  "store_id": "STORE_BLR_002",
  "unique_visitors": 28,
  "conversion_rate": 0.214,
  "avg_dwell_per_zone": {
    "ZONE_SKIN": 94500,
    "ZONE_MAKEUP": 61200,
    "ZONE_BILLING": 43000
  },
  "current_queue_depth": 3,
  "abandonment_rate": 0.12
}
```

### `GET /stores/{store_id}/funnel`

Session-based conversion funnel. Re-entries do not double-count a visitor.

```bash
curl http://localhost:8000/stores/STORE_BLR_002/funnel
```

```json
{
  "stages": [
    {"stage": "total_entries",    "count": 28, "drop_off_pct": 0.0},
    {"stage": "zone_visitors",    "count": 24, "drop_off_pct": 14.3},
    {"stage": "billing_visitors", "count": 12, "drop_off_pct": 50.0},
    {"stage": "purchasers",       "count": 6,  "drop_off_pct": 50.0}
  ]
}
```

### `GET /stores/{store_id}/heatmap`

Zone visit frequency normalised 0–100, with average dwell per zone. Includes `data_confidence: false` when fewer than 20 sessions exist.

```bash
curl http://localhost:8000/stores/STORE_BLR_002/heatmap
```

### `GET /stores/{store_id}/anomalies`

Active operational anomalies with severity and suggested action.

```bash
curl http://localhost:8000/stores/STORE_BLR_002/anomalies
```

```json
{
  "anomalies": [
    {
      "anomaly_type": "BILLING_QUEUE_SPIKE",
      "severity": "WARN",
      "description": "Current queue depth is 6, exceeding threshold of 5.",
      "suggested_action": "Deploy additional billing staff immediately.",
      "detected_at": "2026-04-10T10:42:00Z"
    }
  ]
}
```

### `GET /health`

Service status. Returns `stale_feed: true` for any store with no events in the last 10 minutes.

```bash
curl http://localhost:8000/health
```

---

## Event Flow

```
1. pipeline/detect.py
   └─ OpenCV frame reader → YOLOv8n/m inference (person class)
        └─ ByteTrack → persistent track_id
             └─ Staff HSV classifier (locked on first detection)
                  └─ Zone polygon matching (from store_layout.json)
                       └─ Re-ID (cosine similarity on centroid trajectory)
                            └─ emit.py → events_output.jsonl

2. pipeline/ingest_events.py
   └─ Read events_output.jsonl in batches of 500
        └─ POST /events/ingest
             └─ Pydantic validation
                  └─ INSERT OR IGNORE into SQLite (idempotent)
                       └─ Upsert visitor_sessions

3. GET /stores/{id}/metrics
   └─ Query events WHERE is_staff = 0 AND date = latest event date
        └─ POS correlation: billing zone presence in 5-min window before transaction
             └─ JSON response
```

**POS correlation:** a visitor is counted as converted if they had a `ZONE_ENTER` or `ZONE_DWELL` event in `ZONE_BILLING` within the 5 minutes before any POS transaction for the same store. No `customer_id` linkage exists — correlation is time-window + store-scoped only.

**Why `2026-04-10`?** The Brigade Road CCTV clips and `pos_transactions.csv` are timestamped to 10 April 2026. The API metrics window queries `MAX(timestamp)` date from the database rather than the server's calendar date, so metrics display correctly regardless of when the system is run.

---

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATA_DIR` | `deploy/data` (repo), falls back to `D:\purplle` if present locally | Layout JSON, POS CSV, SQLite DB, seed events |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins for cross-origin requests |
| `PORT` | `8000` | HTTP port (Render and Fly.io set this automatically) |
| `CLIP_REFERENCE_DATE` | `2026-04-10` | Base date for CCTV frame timestamps |
| `API_BASE` | `http://localhost:8000` | Target URL for the ingest script |
| `STAFF_DETECTION_ENABLED` | `true` | Set `false` to treat all detections as customers |

Database path: `{DATA_DIR}/store_intelligence.db`

---

## Tests

```bash
cd store-intelligence
pytest --cov=app tests/
```

Target: >70% statement coverage across all `app/` modules.

Test files and what they cover:

| File | Coverage |
|------|----------|
| `tests/test_pipeline.py` | Single ENTRY, group ENTRY (3 people), REENTRY detection, staff flag, low-confidence emission |
| `tests/test_metrics.py` | Zero-purchase conversion rate, staff exclusion, idempotent ingest, funnel deduplication |
| `tests/test_anomalies.py` | Queue spike at depth 6 (WARN), dead zone after 31 min, conversion drop at 25% below average |

Each test file includes a `# PROMPT:` / `# CHANGES MADE:` block at the top documenting the AI prompt used to generate the tests and what was changed afterwards.

---

## Deployment

See **[HOST.txt](HOST.txt)** for full hosting instructions (Render, Koyeb, Fly.io, Oracle Always Free, Hugging Face Spaces, Google Cloud Run).

**Recommended: Render.com (free web service)**

```bash
# 1. Push to GitHub
git init && git add . && git commit -m "Store Intelligence"
git remote add origin https://github.com/YOUR_USER/store-intelligence.git
git push -u origin main

# 2. Render dashboard → New → Web Service → Connect repo
#    Runtime: Docker | Plan: Free
#    Environment variables:
#      DATA_DIR = /app/deploy/data
#      CORS_ORIGINS = *
```

The repo includes `render.yaml` for Blueprint deploy. Demo data in `deploy/data/` seeds automatically on first start.

> Railway free tier is not available — that plan has ended.

---

## Project Layout

```
store-intelligence/
├── pipeline/
│   ├── detect.py          # YOLOv8 + ByteTrack detection and tracking
│   ├── tracker.py         # Re-ID, staff classification, zone logic
│   ├── emit.py            # Event schema construction and JSONL output
│   ├── ingest_events.py   # Batch POST to /events/ingest
│   ├── run.sh             # Bash: process all clips → ingest
│   └── run_all.ps1        # PowerShell equivalent for Windows
├── app/
│   ├── main.py            # FastAPI entrypoint, middleware, startup
│   ├── models.py          # Pydantic event schema
│   ├── ingestion.py       # Ingest, dedup, session upsert
│   ├── metrics.py         # /metrics endpoint logic
│   ├── funnel.py          # /funnel endpoint + session deduplication
│   ├── anomalies.py       # /anomalies — queue, conversion drop, dead zone
│   └── health.py          # /health — stale feed detection
├── frontend/
│   └── static/            # Light-theme web dashboard (HTML/CSS/JS)
├── dashboard/
│   └── live.py            # Rich terminal dashboard (optional)
├── tests/
│   ├── test_pipeline.py
│   ├── test_metrics.py
│   └── test_anomalies.py
├── docs/
│   ├── DESIGN.md          # Architecture + AI-assisted decisions
│   └── CHOICES.md         # 3 engineering decisions with full reasoning
├── deploy/
│   └── data/              # Bundled demo data (layout, POS, seed events)
├── docker-compose.yml
├── Dockerfile
├── requirements.txt       # Full (pipeline + API)
├── requirements-api.txt   # API only (used in Docker image)
├── render.yaml            # Render Blueprint deploy config
└── HOST.txt               # Free hosting options and instructions
```

---

*Brigade Road store · Dataset date: 10 April 2026*
