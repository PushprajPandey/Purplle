# Store Intelligence — Design Document

> **Project:** End-to-end retail analytics pipeline — CCTV footage → structured events → live conversion metrics API

---

## Table of Contents

1. [System Architecture Overview](#1-system-architecture-overview)
2. [Component Responsibilities](#2-component-responsibilities)
3. [Data Flow](#3-data-flow)
4. [AI-Assisted Decisions](#4-ai-assisted-decisions)
5. [Known Limitations and Graceful Degradation](#5-known-limitations-and-graceful-degradation)

---

## 1. System Architecture Overview

Store Intelligence converts raw CCTV footage from a physical retail store into real-time business analytics centred on a single north-star metric: **offline store conversion rate** — the share of unique visitors who completed a purchase during their session.

The system is built as a four-stage pipeline:

```
CCTV Footage (MP4)
       │
       ▼
┌─────────────────────┐
│   Detection Layer   │  YOLOv8 + ByteTrack + Re-ID
│   pipeline/         │  → structured JSON events
└─────────────────────┘
       │ events_output.jsonl
       ▼
┌─────────────────────┐
│   Intelligence API  │  FastAPI + SQLite
│   app/              │  POST /events/ingest
└─────────────────────┘
       │ SQL queries
       ▼
┌─────────────────────┐
│   Analytics Layer   │  /metrics /funnel /heatmap /anomalies
│   app/metrics.py    │
└─────────────────────┘
       │ HTTP polling
       ▼
┌─────────────────────┐
│   Live Dashboard    │  Web UI at / (light theme)
│   frontend/         │  Rich terminal via dashboard/live.py
└─────────────────────┘
```

Every component is self-contained in a Docker image. A single `docker compose up --build` starts the full API and dashboard with no manual steps beyond `git clone`.

---

## 2. Component Responsibilities

### Detection (`pipeline/detect.py`)

Reads the source MP4 using OpenCV, skips frames proportionally to match a 15fps processing rate, and runs YOLOv8 inference restricted to the **person class only** (class 0). Detections below 0.5 confidence are not dropped — they are forwarded to the tracker with their true confidence score because under-counting traffic is worse than flagging uncertain detections. The camera role (entry vs floor vs billing) is resolved from `store_layout.json`.

### Tracking (`pipeline/tracker.py`)

Implements IoU-based ByteTrack association maintaining active tracks with centroid histories. Every track carries:

- A persistent `track_id` across consecutive frames
- A rolling buffer of the last 10 centroid positions for Re-ID matching
- A locked `is_staff` classification set once on first detection

**Group entry handling:** when three or more bounding boxes cross the entry threshold within a two-second window, each box produces an independent `ENTRY` event — the pipeline never merges simultaneous entries into one count.

### Re-ID (`pipeline/tracker.py — re-entry logic`)

Exited visitors are retained in an `exited_tracks` cache for 30 seconds. When a new detection appears near the entry threshold, its centroid trajectory is compared against cached exits using cosine similarity on the last 10 positions. A match above the similarity threshold emits a `REENTRY` event instead of a fresh `ENTRY`, preventing re-entry inflation — a known problem in retail CV systems.

### Staff Detection (`pipeline/tracker.py — classify_staff`)

On first detection of each track, a dominant-colour histogram is extracted from the bounding box region in HSV space. This is compared against a staff colour profile built from the first two minutes of the entry clip, when the store floor is assumed to be staff-only. Any track matching the profile above an 85% threshold is flagged `is_staff: true` for the lifetime of that session and excluded from all customer-facing metrics.

### Event Emission (`pipeline/emit.py`)

Constructs schema-compliant JSON objects with:

- UUID v4 `event_id` (globally unique per event)
- ISO-8601 UTC timestamps derived from clip start time plus frame offset
- Monotonically incrementing `session_seq` per visitor session
- `dwell_ms: 0` for instantaneous events (not null) so aggregations do not break
- True confidence values — never rounded or suppressed

Events are appended one per line to `pipeline/events_output.jsonl`.

### Intelligence API (`app/`)

FastAPI application backed by SQLite (stdlib `sqlite3`, no ORM). On startup it:

1. Creates tables (`events`, `visitor_sessions`, `pos_transactions`) if absent
2. Reads `store_layout.json` into an in-memory dictionary
3. Loads `pos_transactions.csv` into the database
4. Auto-ingests `deploy/data/events_seed.jsonl` if the events table is empty (enables cold-start demos)

All ingest requests are idempotent via `INSERT OR IGNORE` on `event_id` as primary key. Partial success is supported — malformed events return per-event error detail without rejecting the valid batch.

### Anomaly Detection (`app/anomalies.py`)

Three anomaly rules run on every `/anomalies` request:

| Rule | Trigger | Severity |
|------|---------|---------|
| `BILLING_QUEUE_SPIKE` | Queue depth > 5 | WARN; > 10 → CRITICAL |
| `CONVERSION_DROP` | Today's rate > 20% below 7-day average | WARN; > 40% → CRITICAL |
| `DEAD_ZONE` | No ZONE_ENTER in any zone for 30+ min during open hours | INFO |

Each anomaly includes a `suggested_action` string for operational staff.

### Web Dashboard (`frontend/`)

A single-page light-theme dashboard (violet/teal/coral palette) served as static files by FastAPI at `/`. It polls the API every 3 seconds and renders:

- KPI cards: unique visitors, conversion rate, queue depth, abandonment rate
- Session conversion funnel with drop-off percentages
- Zone heatmap (visit frequency normalised 0–100 with average dwell)
- Active anomaly badges with severity colour coding
- Health/stale-feed banner

No build step required — plain HTML, CSS, and JavaScript mounted at `/static`.

---

## 3. Data Flow

```
Step 1 — Detection
  MP4 file
    └─ OpenCV frame reader (every N frames to hit 15fps)
         └─ YOLOv8n/m inference (person class only)
              └─ ByteTrack association → persistent track_id
                   └─ Staff classification (HSV, locked on first detection)
                        └─ Zone classification (bounding box vs store_layout polygons)
                             └─ Re-ID matching (cosine similarity on centroid trajectory)
                                  └─ emit.py → events_output.jsonl

Step 2 — Ingestion
  events_output.jsonl
    └─ pipeline/ingest_events.py (batch 500 at a time)
         └─ POST /events/ingest
              └─ Pydantic validation → INSERT OR IGNORE into SQLite events table
                   └─ Session upsert in visitor_sessions

Step 3 — Analytics queries
  GET /stores/{id}/metrics
    └─ SELECT from events WHERE is_staff = 0 AND date matches latest event date
         └─ POS correlation (billing zone presence in 5-min window before transaction)
              └─ JSON response: unique_visitors, conversion_rate, avg_dwell_per_zone, ...

Step 4 — Dashboard rendering
  Browser / terminal polls every 2–3 seconds
    └─ Renders KPIs, funnel, heatmap, anomalies
```

**POS Correlation Logic:** a visitor counts as converted if they had a `ZONE_ENTER` or `ZONE_DWELL` event in the billing zone within the 5-minute window immediately before any POS transaction timestamp for the same `store_id`. There is no `customer_id` linkage — correlation is purely time-window and store-scoped, matching the spec.

**Metrics date window:** the API queries the most recent event date present in the database, not the server's calendar date. This ensures metrics display correctly when events from historical clips (e.g. 2026-04-10) are loaded into a deployment running on a later date.

---

## 4. AI-Assisted Decisions

### Decision A — Trajectory Re-ID vs embedding model

**AI suggestion:** Use a lightweight OSNet torchreid embedding model for cross-frame person identity. This is the standard industry approach and offers higher Re-ID accuracy in crowded scenes.

**What was done instead:** Centroid-trajectory cosine similarity matching with a 30-second re-entry window.

**Reasoning for the override:** OSNet requires a 50 MB+ model download and GPU inference for reasonable speed. The challenge spec explicitly describes a "bounding-box-trajectory-based Re-ID" approach, and this solution needs to run on CPU-only assessment machines without extra setup. Trajectory matching produces interpretable, tunable thresholds and satisfies the stated requirement without adding a separate model dependency. The trade-off accepted is lower accuracy when two visitors exit and enter from the same direction within the 30-second window.

---

### Decision B — ZONE_DWELL emission strategy

**AI suggestion:** Emit a single `ZONE_DWELL` event on ZONE_EXIT with the total accumulated duration. This is simpler and produces one event per zone visit.

**What was done instead:** Emit `ZONE_DWELL` every 30 seconds of continuous presence in a zone, as the spec prescribes.

**Reasoning for the override:** The `/anomalies` endpoint needs to detect dead zones and queue buildups in real time — mid-session, before the visitor exits. If dwell is only reported on exit, a visitor standing in the billing zone for 8 minutes would produce no observable signal until they leave. The 30-second tick approach means the live dashboard and anomaly rules react within one dwell cycle. The storage cost of extra dwell events is modest and justified by operational responsiveness.

---

### Decision C — PostgreSQL vs SQLite

**AI suggestion:** Use PostgreSQL from day one for "production readiness." The AI pointed to write concurrency, full-text search, and time-series query performance as reasons.

**What was done instead:** SQLite via stdlib `sqlite3` with no ORM.

**Reasoning for the override:** The challenge spec explicitly states "SQLite is fine" and requests a single `docker compose up` deployment with no external services. SQLite is zero-configuration, file-portable, and handles the write throughput of batched 500-event ingest comfortably. The `INSERT OR IGNORE` idempotency pattern maps cleanly to SQLite's PRIMARY KEY constraint. At the scale of 40 stores with batch ingestion, SQLite serialised writes are not a bottleneck. PostgreSQL migration is the right call when concurrent API replicas, sustained streaming ingestion, or multi-month analytical queries become requirements — that threshold is documented in CHOICES.md.

---

## 5. Known Limitations and Graceful Degradation

| Limitation | Behaviour |
|-----------|-----------|
| Re-ID accuracy in simultaneous re-entries | Two visitors entering from the same direction within 30s may swap identity; confidence values on those events will reflect the detection uncertainty |
| Staff detection calibration | Requires a relatively quiet first 2 minutes on the entry clip; heavy early foot traffic can mis-calibrate the uniform colour profile |
| POS correlation is anonymous | Only time-window + store linkage; cannot trace which specific visitor purchased which basket |
| YOLO on CPU is slow | Long clips take several minutes to process; empty-store periods are handled correctly (no crash, no null returns) |
| CAM 4 produced zero events | Confirmed no human presence in footage; pipeline continues without error |
| Metrics date mismatch | Resolved by querying `MAX(timestamp)` date in DB rather than server calendar date |
| Zero-traffic handling | `/metrics` returns `conversion_rate: 0.0`, not null; `/funnel` returns zero counts at each stage; no 500 errors |
| DB unavailable | API returns `HTTP 503` with structured JSON body including `trace_id`; no raw stack traces exposed |

---

*Document version: 1.0 — Brigade Road store, April 2026 dataset*
