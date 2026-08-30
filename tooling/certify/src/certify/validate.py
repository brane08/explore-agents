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
        if run["verdict"] in ("void", "pending"):
            # void: did not measure the agent. pending: not yet adjudicated —
            # the store's own supervised_streak() (store.py) already excludes
            # pending rows from the count rather than treating them as a
            # streak-breaking gap; a trailing un-adjudicated row must not zero
            # out an otherwise-valid streak here either.
            continue
        if run["verdict"] != "clean":
            break
        streak += 1
    return streak


def _matches_key(run: dict, entry_id: str, version: str, model_profile: str) -> bool:
    """A run dict is scoped to this promotion's key if every key field it
    carries agrees with the key — a run missing a key field entirely is
    treated as already scoped by the caller (existing test fixtures and any
    caller that pre-filters before handing runs in). A run that *does* carry
    a key field with a different value is out of scope: design §4's "evidence
    does not cross the key" rule, so evidence gathered on a different
    version/profile must never count toward this promotion's streak."""
    for field, want in (("entry_id", entry_id), ("entry_version", version),
                        ("model_profile", model_profile)):
        if field in run and run[field] != want:
            return False
    return True


def promote_to_validated(catalog_root: Path, *, entry_id: str, version: str,
                         model_profile: str, runs: list[dict], threshold: int,
                         run_lockbuild: Callable[[Path], None],
                         commit: Callable[[list[Path], str], None]) -> str:
    runs = [r for r in runs if _matches_key(r, entry_id, version, model_profile)]
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
