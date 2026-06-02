#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DATA_DIR="${DATA_DIR:-D:/purplle}"
API_URL="${API_URL:-http://localhost:8000/events/ingest}"
BATCH_SIZE=500

cd "${PROJECT_ROOT}"

find "${DATA_DIR}" -type f \( -iname "*.mp4" -o -iname "*.avi" -o -iname "*.mkv" \) | while read -r video_path; do
  basename="$(basename "${video_path}")"
  store_id="STORE_BLR_002"

  if echo "${basename}" | grep -qi "blr\|brigade"; then
    store_id="STORE_BLR_002"
  fi

  echo "Processing ${video_path} for store ${store_id}"
  python "${SCRIPT_DIR}/detect.py" --video "${video_path}" --store_id "${store_id}"
done

EVENTS_FILE="${DATA_DIR}/store-intelligence/pipeline/events_output.jsonl"
if [[ ! -f "${EVENTS_FILE}" ]]; then
  echo "No events file found at ${EVENTS_FILE}"
  exit 0
fi

python - <<'PY'
import json
import os
import sys
from pathlib import Path

import httpx

data_dir = Path(os.environ.get("DATA_DIR", "D:/purplle"))
events_file = data_dir / "store-intelligence" / "pipeline" / "events_output.jsonl"
api_url = os.environ.get("API_URL", "http://localhost:8000/events/ingest")
batch_size = 500

events = []
with open(events_file, encoding="utf-8") as handle:
    for line in handle:
        line = line.strip()
        if line:
            events.append(json.loads(line))

for start in range(0, len(events), batch_size):
    batch = events[start : start + batch_size]
    response = httpx.post(api_url, json={"events": batch}, timeout=60.0)
    response.raise_for_status()
    print(json.dumps(response.json()))

print(f"Ingested {len(events)} events in batches of {batch_size}")
PY
