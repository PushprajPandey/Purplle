"""POST events from events_output.jsonl to the Store Intelligence API."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_events_output_path

DEFAULT_API_URL = "http://localhost:8000/events/ingest"
BATCH_SIZE = 500


def load_events(path: Path) -> list[dict]:
    events: list[dict] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def ingest_file(path: Path, api_url: str) -> None:
    events = load_events(path)
    if not events:
        print(json.dumps({"error": "no_events", "path": str(path)}))
        sys.exit(1)

    for start in range(0, len(events), BATCH_SIZE):
        batch = events[start : start + BATCH_SIZE]
        response = httpx.post(
            api_url, json={"events": batch}, timeout=120.0
        )
        if response.status_code >= 400:
            print(
                json.dumps(
                    {
                        "error": "ingest_failed",
                        "status_code": response.status_code,
                        "body": response.text[:500],
                        "batch": start // BATCH_SIZE + 1,
                    }
                )
            )
            response.raise_for_status()
        print(json.dumps(response.json()))

    print(json.dumps({"ingested_total": len(events), "path": str(path)}))


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest pipeline JSONL into the API")
    parser.add_argument(
        "--file",
        default=None,
        help="Path to JSONL (default: DATA_DIR/.../events_output.jsonl)",
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("API_URL", DEFAULT_API_URL),
    )
    args = parser.parse_args()
    path = Path(args.file) if args.file else get_events_output_path()
    ingest_file(path, args.api_url)


if __name__ == "__main__":
    main()
