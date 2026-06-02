# Store Intelligence — Technical Choices

## Decision 1 — Detection Model

| Option | Speed | Accuracy | ByteTrack integration |
|--------|-------|----------|------------------------|
| YOLOv8 | Fast (especially n/s variants) | Strong on COCO person class | Native in Ultralytics `model.track()` |
| YOLOv9 | Moderate | Slightly better on small objects | Supported but less documented |
| RT-DETR | Slower transformer inference | High AP, higher latency | Requires adapter pipeline |
| MediaPipe | Very fast on mobile | Lower recall in crowded scenes | No built-in ByteTrack path |

**Choice:** YOLOv8n for entry camera (`CAM_1`) prioritising speed at the door where many people appear briefly. YOLOv8m for floor cameras where occlusion and smaller distant figures need higher accuracy.

**AI suggestion:** Use YOLOv8s uniformly for simpler dependency management.

**Override:** Differentiated n vs m per camera role because entry footage is fixed-angle/high-throughput while floor aisles need richer features—worth the extra model load only where needed.

---

## Decision 2 — Event Schema Design

**`session_seq` in metadata:** Orders events within a visitor session for debugging and downstream session reconstruction without re-sorting by timestamp alone (clock skew across cameras).

**Confidence never suppressed:** Partial occlusion often yields 0.3–0.5 confidence; dropping those detections would under-count traffic. We emit all detections and preserve the model score for analytics quality filters later.

**`dwell_ms = 0` for instantaneous events:** Distinguishes “no dwell measured” from “unknown/null” which would break aggregation (`AVG(dwell_ms)`). ENTRY/EXIT/ZONE_ENTER are point-in-time.

**Fine-grained vs aggregated events:** Fine-grained events (per crossing, per 30s dwell tick, queue join/abandon) increase storage but enable funnel, heatmap, and anomaly rules without reprocessing video. Aggregated-only would lose queue abandonment timing.

**AI suggestion:** Collapse ZONE_DWELL into a single summary on EXIT.

**Personal reasoning:** Live queue and dead-zone anomalies need mid-session signals; waiting for EXIT is too late for staffing alerts.

---

## Decision 3 — API Storage Choice

**SQLite** via stdlib `sqlite3`: zero external service, single file at `store-intelligence/app/store_intelligence.db`, ideal for Docker Compose assessment and laptop demos.

**Write concurrency:** SQLite handles concurrent readers well but serialises writers. Ingest batches of 500 events are short transactions—acceptable for store-scale volume.

**Idempotency:** `INSERT OR IGNORE` on `event_id` PRIMARY KEY plus application-level duplicate checks ensures re-running `run.sh` or retrying ingest does not duplicate rows.

**Migration trigger:** Move to PostgreSQL when multi-store write throughput exceeds ~100 sustained ingests/second, when horizontal API replicas are required, or when analytics queries need complex joins across months of data without archival.

**AI suggestion:** PostgreSQL from day one for “production readiness.”

**Final reasoning:** The spec explicitly requests SQLite and direct sql usage; operational simplicity outweighs hypothetical scale for this deliverable.
