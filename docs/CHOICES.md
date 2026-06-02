# Store Intelligence — Technical Choices

> Three engineering decisions, each documented with options considered, what AI suggested, what was chosen, and the personal reasoning behind the choice.

---

## Decision 1 — Detection Model Selection

### Options Evaluated

| Model | Speed | Accuracy | ByteTrack Integration | Notes |
|-------|-------|----------|----------------------|-------|
| **YOLOv8n** | Very fast (CPU-viable) | Good on large/close persons | Native via `model.track()` | Best for entry cameras — high-throughput, fixed angle |
| **YOLOv8m** | Moderate | Strong on partially occluded/distant figures | Native | Best for floor cameras — needs richer features |
| **YOLOv8s** | Fast | Balanced | Native | Uniform option, less optimised per camera role |
| **YOLOv9** | Moderate | Slightly better on small objects | Supported but less documented | Higher setup cost, marginal gain for this use case |
| **RT-DETR** | Slow (transformer inference) | High AP, especially dense crowds | Requires custom adapter | Unjustified latency for CPU-only assessment machines |
| **MediaPipe** | Very fast (mobile-optimised) | Lower recall in crowded retail scenes | No native ByteTrack path | Too much Re-ID plumbing required from scratch |

### What AI Suggested

Use **YOLOv8s uniformly** across all cameras. The reasoning given was simpler dependency management — one model weight file, consistent confidence calibration, and less conditional logic in the detection script.

### What Was Chosen

**YOLOv8n for entry camera (CAM 1), YOLOv8m for floor and billing cameras (CAM 2, 3, 5).**

### Personal Reasoning

The entry camera operates at a fixed angle watching a narrow threshold zone. People appear briefly, often in partial view, and the most important signal is the crossing direction rather than fine-grained pose detail. YOLOv8n handles this at much higher throughput per second of video, which matters when processing 20-minute clips on CPU.

The floor and billing cameras see a wider field with multiple people at varying distances, partial occlusion from displays, and the billing queue scenario where accurate individual bounding boxes determine queue depth counting. YOLOv8m's additional feature capacity reduces false merges in crowded frames.

The AI's suggestion of a single uniform model would have been correct if the pipeline ran on GPU where latency is not the binding constraint. On CPU, the per-camera model selection is a meaningful optimisation. The extra conditional logic in `detect.py` to load different weights per camera role is about 12 lines and well worth the throughput improvement at the entry stage.

One point where I agreed with the AI: YOLOv9 was not worth the integration overhead. Its accuracy advantage over YOLOv8m on standard COCO benchmarks is incremental, and the Ultralytics YOLOv8 ecosystem (native ByteTrack in `model.track()`, consistent Pydantic-friendly result objects) reduced pipeline complexity significantly.

---

## Decision 2 — Event Schema Design

### The Core Choices

**a) `session_seq` in metadata**

Every event carries an ordinal `session_seq` that increments for each event within a visitor session. The primary purpose is debuggability — when reviewing a session in the database, events can be ordered by `session_seq` rather than `timestamp` alone, which protects against minor clock skew between cameras producing confusing orderings. A secondary benefit is that the funnel endpoint can quickly detect a "billing before zone visit" anomaly in the event sequence without reconstructing the full session from raw timestamps.

**b) Confidence is never suppressed**

Low-confidence detections (0.25–0.50, common in partial occlusion scenarios) are included in emitted events with their true model score preserved in the `confidence` field. There are two reasons for this. First, the spec explicitly states confidence must "degrade gracefully, not fail silently." Second, dropping sub-threshold detections systematically under-counts traffic in the billing queue area where occlusion is most frequent. Operators and downstream systems can apply their own confidence filters on the stored events; the pipeline's job is accurate capture, not editorial filtering.

**c) `dwell_ms: 0` for instantaneous events, not `null`**

`ENTRY`, `EXIT`, `ZONE_ENTER`, and `ZONE_EXIT` events represent a moment in time rather than a duration. Using `0` instead of `null` means `AVG(dwell_ms)` queries in the metrics layer produce clean numeric results rather than requiring `COALESCE` handling throughout. It also makes schema validation simpler — `dwell_ms` is always an integer, never nullable.

**d) Fine-grained events vs aggregated summary events**

The schema emits discrete events for every crossing, every 30-second dwell tick, every queue join, and every abandonment rather than producing aggregated summaries at session end. The operational justification is that the anomaly layer needs mid-session signals: a dead zone with no visits for 30 minutes during trading hours cannot be detected if events are only written on zone exit. Similarly, a queue spike needs the current in-zone count in near real time, not a post-session summary.

### What AI Suggested

Collapse `ZONE_DWELL` into a single summary event emitted on `ZONE_EXIT` with the total accumulated dwell duration. The argument was reduced storage overhead and simpler session reconstruction logic.

### What Was Chosen

Per-30-second `ZONE_DWELL` ticks as prescribed in the challenge spec.

### Personal Reasoning

The AI's collapsed-dwell approach is sensible for offline batch analytics where you only need historical summaries. It is wrong for a live operations system where the `/anomalies` endpoint needs to know that a zone has been occupied for the last 45 seconds with no new entries — a signal that only exists if dwell events are emitted while the visitor is still present.

The storage cost is real but manageable: a visitor dwelling in one zone for 10 minutes produces 20 ZONE_DWELL events instead of 1. At the scale of a single store with a few dozen simultaneous visitors, this is negligible. The schema was designed for operational visibility over storage efficiency, and that trade-off is the right one given the challenge's north star metric (conversion rate) depends on billing zone timing precision.

---

## Decision 3 — API Storage Architecture

### Options Considered

| Option | Pros | Cons |
|--------|------|------|
| **SQLite (stdlib)** | Zero external dependencies, single file, `INSERT OR IGNORE` idempotency is clean, works in Docker with no extra services | Serialised writers; not suitable for multi-replica horizontal scaling |
| **PostgreSQL** | Write concurrency, advanced indexing, time-series extensions, production standard | Requires separate container, more complex compose file, heavier setup for reviewers |
| **Redis (event cache) + PostgreSQL** | High-throughput ingest, rich queries | Significant extra complexity; two moving parts to fail |
| **DuckDB** | Excellent for analytical queries | Relatively newer ecosystem, less Pydantic/FastAPI integration material |

### What AI Suggested

PostgreSQL from day one. The stated reasoning was "production readiness" — write concurrency when multiple stores POST events simultaneously, proper index types (`BRIN` for time-series), and connection pooling for API replicas.

### What Was Chosen

**SQLite via stdlib `sqlite3`, no ORM.**

### Personal Reasoning

The challenge spec states explicitly: "SQLite is fine." It also requires `docker compose up` to start everything with no manual steps beyond `git clone`. A PostgreSQL setup would add a second container, a health-check dependency in `docker-compose.yml`, migration scripts, and connection string configuration — all of which create failure surfaces for a reviewer doing a cold evaluation.

The write concurrency concern the AI raised is legitimate in production but not binding here. Ingest happens in batches of up to 500 events per POST request, each batch being a short transaction. ByteTrack + YOLOv8 on CPU processes one clip at a time; the bottleneck is video decoding, not database writes. SQLite serialised writes are not the constraint.

Idempotency is actually cleaner in SQLite than in PostgreSQL for this use case. `INSERT OR IGNORE` on a `TEXT PRIMARY KEY` (`event_id` UUID) is a single statement with no risk of partial-upsert logic bugs. The same pattern in PostgreSQL would use `ON CONFLICT DO NOTHING`, which is equivalent but requires awareness of table-level locking semantics in concurrent workloads.

**Migration trigger:** the right moment to move to PostgreSQL is when any of the following conditions hold:

1. Multiple API replicas need to write simultaneously without serialisation
2. Sustained ingest rate exceeds approximately 100 events per second continuously
3. Analytics queries span months of data requiring window functions over millions of rows
4. The system expands to all 40 Apex Retail stores with real-time streaming from all cameras

None of those conditions apply to the current deliverable, which is a single-store, single-container, batch-ingest demo system.

---

*Document version: 1.0 — Brigade Road store, April 2026 dataset*
