"""Application configuration loaded from environment."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BUNDLED_DATA_DIR = _REPO_ROOT / "deploy" / "data"
# Legacy local dev when CCTV + CSV live next to the repo parent folder
_LEGACY_DATA_DIR = _REPO_ROOT.parent


def get_data_dir() -> Path:
    if os.environ.get("DATA_DIR"):
        return Path(os.environ["DATA_DIR"])
    if (_BUNDLED_DATA_DIR / "store_layout.json").is_file():
        return _BUNDLED_DATA_DIR
    if (_LEGACY_DATA_DIR / "store_layout.json").is_file():
        return _LEGACY_DATA_DIR
    return _BUNDLED_DATA_DIR


def get_db_path() -> Path:
    data_dir = get_data_dir()
    if (data_dir / "store_layout.json").is_file():
        db_path = data_dir / "store_intelligence.db"
    else:
        db_path = data_dir / "store-intelligence" / "app" / "store_intelligence.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def get_store_layout_path() -> Path:
    return get_data_dir() / "store_layout.json"


def get_pos_transactions_path() -> Path:
    return get_data_dir() / "pos_transactions.csv"


def get_events_output_path() -> Path:
    data_dir = get_data_dir()
    for name in ("events_output.jsonl", "events_seed.jsonl"):
        candidate = data_dir / name
        if candidate.is_file():
            return candidate
    path = _REPO_ROOT / "pipeline" / "events_output.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_store_layout() -> dict[str, Any]:
    with open(get_store_layout_path(), encoding="utf-8") as handle:
        return json.load(handle)


def get_store_config(store_id: str) -> dict[str, Any] | None:
    layout = load_store_layout()
    for store in layout.get("stores", []):
        if store["store_id"] == store_id:
            return store
    return None


def get_cors_origins() -> list[str]:
    raw = os.environ.get("CORS_ORIGINS", "*").strip()
    if not raw or raw == "*":
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]
