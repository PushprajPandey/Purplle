# Store Intelligence

End-to-end retail analytics: CCTV → person detection → visitor events → conversion metrics API.

## Quick start (5 commands)

```bash
git clone <repo>
cd store-intelligence
pip install -r requirements.txt
docker compose up --build
```

Open the **web dashboard**: [http://localhost:8000/](http://localhost:8000/) (light-theme UI, auto-refreshes every 3s).

Then process footage:

```bash
python pipeline/detect.py --video D:\purplle\CCTV Footage-20260529T160731Z-3-00144614ea\CCTV Footage\CAM 1.mp4 --store_id STORE_BLR_002
```

## Event flow

1. **`pipeline/detect.py`** reads CCTV MP4, runs YOLOv8 + ByteTrack, applies Re-ID/staff/zone logic, and writes **`pipeline/events_output.jsonl`** (one JSON event per line).
2. **`pipeline/run.sh`** processes all videos under `DATA_DIR`, then POSTs events to **`POST /events/ingest`** in batches of 500.
3. The **FastAPI** app validates events, deduplicates by `event_id`, stores rows in **SQLite**, and updates **`visitor_sessions`**.
4. **POS transactions** from `DATA_DIR/pos_transactions.csv` load on startup for conversion correlation.
5. Query **`GET /stores/{store_id}/metrics`**, **`/funnel`**, **`/heatmap`**, **`/anomalies`** for analytics.
6. **Web dashboard** at **`http://localhost:8000/`** — KPIs, funnel, zone heatmap, anomalies (Part E bonus).
7. **`dashboard/live.py`** — optional Rich terminal dashboard (2s refresh).

### Run without Docker

```bash
cd store-intelligence
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Process all 5 cameras (recommended):

```powershell
.\pipeline\run_all.ps1 -Overwrite
```

Manual batch (CAM 1 uses `--overwrite`, others **append** automatically):

```powershell
$cam = "D:\purplle\CCTV Footage-20260529T160731Z-3-00144614ea\CCTV Footage"
python pipeline/detect.py --video "$cam\CAM 1.mp4" --store_id STORE_BLR_002 --overwrite
python pipeline/detect.py --video "$cam\CAM 2.mp4" --store_id STORE_BLR_002
python pipeline/detect.py --video "$cam\CAM 3.mp4" --store_id STORE_BLR_002
python pipeline/detect.py --video "$cam\CAM 4.mp4" --store_id STORE_BLR_002
python pipeline/detect.py --video "$cam\CAM 5.mp4" --store_id STORE_BLR_002
python pipeline/ingest_events.py
```

Disable staff colour detection (all customers): `$env:STAFF_DETECTION_ENABLED="false"`

In another terminal:

```bash
python dashboard/live.py
```

| UI | URL |
|----|-----|
| **Web dashboard (frontend)** | http://localhost:8000/ |
| Swagger API | http://localhost:8000/docs |
| API metadata | http://localhost:8000/api |

### Frontend

The **`frontend/`** folder is a single-page **light-theme** dashboard (violet / teal / coral palette) served by FastAPI. It shows:

- Unique visitors, conversion rate, queue depth, abandonment
- Session conversion funnel with drop-off %
- Zone heatmap (visit frequency 0–100 + avg dwell)
- Active anomalies with severity badges
- Health / stale-feed banner

No separate build step — static HTML/CSS/JS mounted at `/static`.

## Deploy to GitHub & free hosting

See **[HOST.txt](HOST.txt)** for step-by-step hosting (Render, Koyeb, Fly.io, Oracle Always Free, etc.). **Railway free tier is not listed** — that plan has ended.

Bundled demo data lives in `deploy/data/` (layout, POS, `events_seed.jsonl`). The API auto-ingests on first start when the DB is empty.

```bash
git init && git add . && git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USER/store-intelligence.git
git push -u origin main
```

Cloud Docker image uses `requirements-api.txt` (API only). Local pipeline uses full `requirements.txt`.

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATA_DIR` | `deploy/data` in repo, else parent `D:\purplle` if present | Layout, POS, DB, seed events |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins for cross-origin API calls |
| `PORT` | `8000` | HTTP port (Render/Fly set this automatically) |
| `CLIP_REFERENCE_DATE` | `2026-04-10` | CCTV event timestamps (matches Brigade POS CSV) |
| `API_BASE` | `http://localhost:8000` | Ingest script target URL |

Database path: `{DATA_DIR}/store_intelligence.db` (bundled layout) or legacy `{DATA_DIR}/store-intelligence/app/store_intelligence.db`

**Why 2026-04-10?** The Brigade CCTV and `pos_transactions.csv` are from **10 April 2026**. Event timestamps use that clip day so POS correlation works. The API metrics window uses the **latest event date in the database**, not your PC's calendar date.

## Tests

```bash
cd store-intelligence
pytest --cov=app tests/
```

Target: >70% statement coverage on `app/` modules.

## Project layout

```
store-intelligence/
├── pipeline/     # detect, track, emit, run.sh
├── app/          # FastAPI + SQLite
├── frontend/     # Web dashboard (light theme, served at /)
├── dashboard/    # Rich terminal UI (optional)
├── tests/
└── docs/
```

See `docs/DESIGN.md` and `docs/CHOICES.md` for architecture and trade-offs.
