"""quarantined → validated promotion from supervised-run evidence.

CATALOG §7: promotion requires N supervised runs meeting the divergence/incident
threshold. The orchestrator accumulates the runs; certify is the only writer of
trust.yaml, so the decision lands here. The record carries a content-addressed
digest of the exact evidence set, which makes the promotion reproducible: the
same runs always hash to the same value.

Design record: design/2026-08-13-shadow-divergence-design.md
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import yaml

_DIGEST_FIELDS = ("run_id", "invocation_id", "mode", "verdict", "reason",
                  "adjudicated_by")


def evidence_digest(runs: list[dict]) -> str:
    """sha256 over the canonical evidence set — sorted keys, fixed field list,
    LF-joined, so the digest never depends on dict ordering or extra columns."""
    canonical = [
        {k: run.get(k) for k in _DIGEST_FIELDS}
        for run in sorted(runs, key=lambda r: r["run_id"])
    ]
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _trailing_clean(runs: list[dict]) -> int:
    streak = 0
    for run in sorted(runs, key=lambda r: r["run_id"], reverse=True):
        if run["verdict"] == "void":
            continue                      # did not measure the agent
        if run["verdict"] != "clean":
            break
        streak += 1
    return streak


def promote_to_validated(catalog_root: Path, *, entry_id: str, version: str,
                         model_profile: str, runs: list[dict], threshold: int,
                         run_lockbuild: Callable[[Path], None],
                         commit: Callable[[list[Path], str], None]) -> str:
    streak = _trailing_clean(runs)
    if streak < threshold:
        raise ValueError(
            f"{entry_id} has {streak} clean runs, needs {threshold} — not eligible")

    digest = evidence_digest(runs)
    trust_path = catalog_root / "trust.yaml"
    data = yaml.safe_load(trust_path.read_text(encoding="utf-8")) or {}
    records = list(data.get("records") or [])
    records.append({
        "id": entry_id,
        "version": version,
        "model_profile": model_profile,
        "tier": "validated",
        "granted_by": f"supervised-{threshold}",
        "evidence": f"supervised:{digest}",
    })
    data["records"] = records
    trust_path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")

    run_lockbuild(catalog_root)
    commit([trust_path, catalog_root / "catalog.lock.yaml"],
           f"certify: promote {entry_id} to validated ({threshold} supervised runs)")
    return digest
