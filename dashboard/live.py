"""Live terminal dashboard for Store Intelligence metrics."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text

from app.config import load_store_layout

API_BASE = os.environ.get("API_BASE", "http://localhost:8000")
REFRESH_SECONDS = 2


def fetch_store_ids() -> list[str]:
    layout = load_store_layout()
    return [store["store_id"] for store in layout.get("stores", [])]


def fetch_metrics(store_id: str) -> dict:
    response = httpx.get(f"{API_BASE}/stores/{store_id}/metrics", timeout=10.0)
    response.raise_for_status()
    return response.json()


def fetch_anomalies(store_id: str) -> list[dict]:
    response = httpx.get(f"{API_BASE}/stores/{store_id}/anomalies", timeout=10.0)
    response.raise_for_status()
    return response.json()


def build_table() -> Table:
    table = Table(title="Store Intelligence Live Dashboard")
    table.add_column("Store ID", style="cyan")
    table.add_column("Unique Visitors", justify="right")
    table.add_column("Conversion %", justify="right")
    table.add_column("Queue Depth", justify="right")
    table.add_column("Active Anomalies", justify="right")

    store_ids = fetch_store_ids()
    max_severity_by_store: dict[str, str] = {}

    for store_id in store_ids:
        metrics = fetch_metrics(store_id)
        anomalies = fetch_anomalies(store_id)
        critical_count = sum(1 for a in anomalies if a["severity"] == "CRITICAL")
        warn_count = sum(1 for a in anomalies if a["severity"] == "WARN")
        active_count = len(anomalies)

        if critical_count > 0:
            max_severity_by_store[store_id] = "CRITICAL"
        elif warn_count > 0:
            max_severity_by_store[store_id] = "WARN"
        else:
            max_severity_by_store[store_id] = "OK"

        conversion_pct = metrics["conversion_rate"] * 100.0
        row_style = None
        if max_severity_by_store[store_id] == "CRITICAL":
            row_style = "bold red"
        elif max_severity_by_store[store_id] == "WARN":
            row_style = "bold yellow"

        table.add_row(
            store_id,
            str(metrics["unique_visitors"]),
            f"{conversion_pct:.1f}%",
            str(metrics["current_queue_depth"]),
            str(active_count),
            style=row_style,
        )

    last_updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    header = Text(f"Last updated: {last_updated}", style="dim")
    table.caption = str(header)
    return table


def main() -> None:
    console = Console()
    with Live(build_table(), console=console, refresh_per_second=1 / REFRESH_SECONDS) as live:
        while True:
            try:
                live.update(build_table())
            except httpx.HTTPError as exc:
                console.print(f"[red]API error: {exc}[/red]")
            import time

            time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    main()
