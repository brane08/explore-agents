"""Fixtures: a throwaway git catalog repo seeded from the real one, and a
TestClient app wired with the deterministic scorer + a fake tool invoker."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lockbuild.build import LOCK_FILENAME, build_lock, render_lock
from orchestrator.config import Settings
from orchestrator.scoring import lexical_scorer
from orchestrator.store import SQLiteStore
from orchestrator.webapp import create_app

REPO_ROOT = Path(__file__).parents[3]

TASK = "search the log records for level ERROR entries"
GIBBERISH = "arrange my quarterly offsite travel itinerary"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


def rebuild_and_commit(root: Path, message: str) -> str:
    (root / LOCK_FILENAME).write_text(render_lock(build_lock(root)), encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def catalog_repo(tmp_path: Path) -> Path:
    root = tmp_path / "catalog"
    root.mkdir()
    for d in ("skills", "mcp", "models"):
        shutil.copytree(REPO_ROOT / d, root / d)
    for f in ("tags.yaml", "trust.yaml"):
        shutil.copy(REPO_ROOT / f, root / f)
    (root / "agents" / "_proposed").mkdir(parents=True)
    (root / "templates").mkdir()
    (root / "a2a").mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "seed")
    rebuild_and_commit(root, "lock")
    return root


@pytest.fixture
def settings(catalog_repo: Path, tmp_path: Path) -> Settings:
    return Settings(
        catalog_root=catalog_repo,
        db_path=tmp_path / "orch.sqlite3",
        secret_key="test-secret",
        operator_users=("op@example.com",),
    )


@pytest.fixture
def store(settings: Settings) -> SQLiteStore:
    return SQLiteStore(settings.db_path)


def fake_invoker(tool_id: str, args: dict) -> dict:
    return {"ok": True, "tool": tool_id, "args": args}


@pytest.fixture
def client(settings: Settings, store: SQLiteStore) -> TestClient:
    app = create_app(settings, store=store, scorer=lexical_scorer,
                     invoke_tool=fake_invoker)
    return TestClient(app)


def add_skill(root: Path, sid: str, summary: str) -> None:
    d = root / "skills" / sid
    (d / "impl").mkdir(parents=True)
    (d / "entry.yaml").write_text(
        f"id: {sid}\nkind: skill\nversion: 1.0.0\n"
        f"routing_summary: {summary}\n"
        "capability_tags: [read-only]\n"
        f"detail: {summary}\n",
        encoding="utf-8",
    )
    (d / "SKILL.md").write_text(f"---\nname: {sid}\n---\n{summary}\n", encoding="utf-8")
    (d / "impl" / "run.py").write_text("pass\n", encoding="utf-8")


def grant_validated(root: Path, sid: str, profile: str = "stub-class-ref") -> None:
    import yaml
    trust = root / "trust.yaml"
    data = yaml.safe_load(trust.read_text(encoding="utf-8"))
    data["records"].append({
        "id": sid, "version": "1.0.0", "model_profile": profile,
        "tier": "validated", "granted_by": "test", "evidence": "test",
    })
    trust.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")


def demote_all(root: Path, tier: str = "quarantined") -> None:
    import yaml
    trust = root / "trust.yaml"
    data = yaml.safe_load(trust.read_text(encoding="utf-8"))
    for r in data["records"]:
        r["tier"] = tier
    trust.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
