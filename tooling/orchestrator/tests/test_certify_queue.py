"""Certify-queue meta screen (UI-PLANE §3.2, CHECKLISTS layer 10).

The human-promotion surface: _proposed/ candidates with step evidence and
binding audit; promote produces the real atomic promotion commit (certify
steps behind it), reject clears the candidate. Operator-gated.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from orch_fixtures import git
from orchestrator.dispatch import build_inputs, dispatch, stub_harness
from orchestrator.lockload import current_ref, load_lock_at

HARNESS = "stub-harness/0"
TASK = "list error-level log records for a service"
CRITERIA = "BC-1: returns only records whose level equals ERROR.\n"

OP = {"X-Forwarded-Roles": "operator"}


@pytest.fixture
def candidate(catalog_repo: Path, settings) -> str:
    """A real stub candidate in _proposed/, with its frozen-inputs sidecar."""
    (catalog_repo / "conformance.yaml").write_text(yaml.safe_dump({
        "harnesses": [{"harness": HARNESS, "conformant": True,
                       "recorded_at": "2026-07-16T00:00:00+00:00"}],
    }), encoding="utf-8")
    ref = current_ref(catalog_repo)
    lock = load_lock_at(catalog_repo, ref)
    result = dispatch(build_inputs(TASK, CRITERIA, ref, lock, settings, HARNESS),
                      catalog_repo, stub_harness)
    assert result.outcome == "candidate", result.refusal
    return result.candidate_dir.name


# The stub harness is honest about being a stub: it declares no slot surface and
# ships an eval runner that reports nothing, so its output is deliberately *not*
# promotable. A candidate that a real harness would have produced differs in
# exactly those two respects.
_REAL_EVAL_RUNNER = """\
import json, os, sys

criteria = [{"id": "BC-1", "passed": True}]
report = {"model_profile": os.environ.get("MODEL_PROFILE", ""), "criteria": criteria}
open(os.environ["EVAL_REPORT"], "w", encoding="utf-8").write(json.dumps(report))
sys.exit(0)
"""


@pytest.fixture
def certified_candidate(candidate: str, catalog_repo: Path) -> str:
    cdir = catalog_repo / "agents" / "_proposed" / candidate
    manifest_file = cdir / "AGENT_MANIFEST.yaml"
    manifest = yaml.safe_load(manifest_file.read_text(encoding="utf-8"))
    manifest["slot_value_surface"] = [
        {"name": "level", "type": "value", "location": "config.level"}]
    manifest_file.write_text(yaml.safe_dump(manifest, sort_keys=True), encoding="utf-8")
    (cdir / "eval" / "run.py").write_text(_REAL_EVAL_RUNNER, encoding="utf-8")
    return candidate


def test_queue_gated_by_operator_role(client, candidate):
    client.get("/")
    assert client.get("/certify").status_code == 403
    assert client.get("/certify", headers=OP).status_code == 200


def test_queue_lists_proposed_candidates(client, candidate):
    client.get("/")
    r = client.get("/certify", headers=OP)
    assert candidate in r.text


def _manifest(catalog_repo: Path, cid: str) -> dict:
    return yaml.safe_load(
        (catalog_repo / "agents" / "_proposed" / cid / "AGENT_MANIFEST.yaml")
        .read_text(encoding="utf-8"))


def test_detail_shows_step_evidence_and_binding_audit(client, candidate, catalog_repo):
    client.get("/")
    r = client.get(f"/certify/{candidate}", headers=OP)
    assert r.status_code == 200
    for step in ("layout_manifest", "rebase", "structural", "security_static", "eval"):
        assert step in r.text
    # binding audit: every bound entry rendered with its live tier
    bound = [b["id"] for b in _manifest(catalog_repo, candidate)["bindings"]]
    assert bound
    for bid in bound:
        assert bid in r.text
    assert "validated" in r.text


def test_promote_refuses_a_candidate_that_fails_the_mechanical_steps(
        client, candidate, catalog_repo):
    """The gate is live on the real dispatch path, not only in unit tests: the
    stub's own output cannot be promoted, and the refusal names why."""
    client.get("/")
    agent_id = _manifest(catalog_repo, candidate)["agent_id"]
    r = client.post(f"/certify/{candidate}/promote", headers=OP)
    assert r.status_code == 409
    assert "eval" in r.text
    assert not (catalog_repo / "agents" / agent_id).exists()
    assert (catalog_repo / "agents" / "_proposed" / candidate).is_dir()


def test_promote_produces_atomic_promotion(client, certified_candidate, catalog_repo):
    candidate = certified_candidate
    client.get("/")
    agent_id = _manifest(catalog_repo, candidate)["agent_id"]
    r = client.post(f"/certify/{candidate}/promote", headers=OP)
    assert r.status_code == 200, r.text
    promoted = catalog_repo / "agents" / agent_id
    assert promoted.is_dir() and (promoted / "entry.yaml").is_file()
    assert not (catalog_repo / "agents" / "_proposed" / candidate).exists()
    records = yaml.safe_load(
        (catalog_repo / "trust.yaml").read_text(encoding="utf-8"))["records"]
    mine = [x for x in records if x["id"] == agent_id]
    assert mine and mine[-1]["tier"] == "quarantined"
    # one real commit carrying the promotion
    last = git(catalog_repo, "log", "-1", "--format=%s")
    assert "promote" in last and agent_id in last
    # lock rebuilt in the same commit: promoted entry present at HEAD's lock
    lock = load_lock_at(catalog_repo, current_ref(catalog_repo))
    assert any(e["id"] == agent_id for e in lock["entries"])
    porcelain = git(catalog_repo, "status", "--porcelain")
    assert porcelain == "", f"promotion must leave a clean tree, got: {porcelain}"


def test_promote_requires_operator(client, candidate):
    client.get("/")
    assert client.post(f"/certify/{candidate}/promote").status_code == 403


def test_reject_clears_candidate_and_sidecar(client, candidate, catalog_repo):
    client.get("/")
    r = client.post(f"/certify/{candidate}/reject",
                    data={"reason": "criteria too weak"}, headers=OP)
    assert r.status_code == 200
    proposed = catalog_repo / "agents" / "_proposed"
    assert not (proposed / candidate).exists()
    assert not (proposed / f"{candidate}.inputs.yaml").exists()
