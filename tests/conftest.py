"""Pytest fixtures for Store Intelligence tests."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
DEPLOY_DATA = ROOT / "deploy" / "data"
LEGACY_DATA = ROOT.parent
if (LEGACY_DATA / "store_layout.json").is_file():
    DATA_ROOT = LEGACY_DATA
elif (DEPLOY_DATA / "store_layout.json").is_file():
    DATA_ROOT = DEPLOY_DATA
else:
    DATA_ROOT = LEGACY_DATA

os.environ.setdefault("DATA_DIR", str(DATA_ROOT))
os.environ["SEED_DEMO_DATA"] = "false"


@pytest.fixture()
def test_db_path(tmp_path, monkeypatch) -> Path:
    db_path = tmp_path / "store_intelligence.db"
    monkeypatch.setenv("DATA_DIR", str(DATA_ROOT))
    monkeypatch.setattr("app.config.get_db_path", lambda: db_path)
    return db_path


@pytest.fixture()
def client(test_db_path, monkeypatch) -> TestClient:
    from app.database import init_database, load_pos_transactions
    from app import main as main_module

    monkeypatch.setattr("app.config.get_db_path", lambda: test_db_path)
    monkeypatch.setattr("app.database.get_db_path", lambda: test_db_path)
    monkeypatch.setattr("app.database.utc_today", lambda: "2026-04-10")

    def fixed_analytics_date(cursor, store_id: str) -> str:
        return "2026-04-10"

    monkeypatch.setattr("app.database.get_analytics_date", fixed_analytics_date)
    monkeypatch.setattr("app.metrics.get_analytics_date", fixed_analytics_date)
    monkeypatch.setattr("app.funnel.get_analytics_date", fixed_analytics_date)
    monkeypatch.setattr("app.anomalies.get_analytics_date", fixed_analytics_date)

    init_database(test_db_path)
    load_pos_transactions(test_db_path)
    from app.config import load_store_layout

    monkeypatch.setattr(main_module, "STORE_LAYOUT", load_store_layout())

    with TestClient(main_module.app) as test_client:
        yield test_client


@pytest.fixture()
def store_config() -> dict:
    layout_path = DATA_ROOT / "store_layout.json"
    with open(layout_path, encoding="utf-8") as handle:
        layout = json.load(handle)
    return layout["stores"][0]
