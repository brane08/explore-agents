"""GET /health — checks DB and catalog-lock reachability, not just liveness."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from orchestrator.config import Settings
from orchestrator.store import SQLiteStore
from orchestrator.webapp import create_app


def test_health_ok_when_db_and_lock_reachable(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"]["db"] == "ok"
    assert body["checks"]["catalog_lock"] == "ok"


def test_health_reports_unreachable_catalog(tmp_path: Path):
    # catalog_root is not a git repo at all: current_ref()/load_lock_at() fail
    broken_root = tmp_path / "not-a-catalog"
    broken_root.mkdir()
    settings = Settings(catalog_root=broken_root, db_path=tmp_path / "orch.sqlite3",
                        secret_key="test-secret")
    app = create_app(settings, store=SQLiteStore(settings.db_path))
    r = TestClient(app).get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "error"
    assert body["checks"]["catalog_lock"].startswith("error:")
    assert body["checks"]["db"] == "ok"
