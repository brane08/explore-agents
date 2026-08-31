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
from lockbuild.trust import effective_tier

_DIGEST_FIELDS = ("run_id", "invocation_id", "entry_id", "entry_version",
                  "model_profile", "mode", "verdict", "reason", "counterpart_id",
                  "adjudicated_by", "adjudicated_at", "created_at")


def evidence_digest(runs: list[dict]) -> str:
    """sha256 over the canonical evidence set.

    Each row is serialized to a canonical (sorted-key) JSON string first, and
    *those strings* are sorted before joining — not the raw dicts by `run_id`
    — so the digest is invariant under input-list reordering, duplicate
    `run_id`s, and `run_id`s of mixed/uncomparable type (a JSON export may
    stringify what the store stores as an int)."""
    canonical = [
        json.dumps({k: run.get(k) for k in _DIGEST_FIELDS},
                   sort_keys=True, separators=(",", ":"))
        for run in runs
    ]
    blob = json.dumps(sorted(canonical), separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _sorted_most_recent_first(runs: list[dict]) -> list[dict]:
    """Order the trailing-streak walk by `created_at` (design §3's row shape;
    ISO-8601 sorts lexicographically) when every row carries it. `run_id` is
    a store implementation detail (an autoincrement int) that a JSON export
    may re-serialize as a string, silently reversing the intended order and
    letting a trailing incident promote (M5) — fall back to it only when
    `created_at` is unavailable, and fail closed on an uncomparable mix
    rather than raise a bare TypeError past the CLI's error handling."""
    if all(r.get("created_at") for r in runs):
        return sorted(runs, key=lambda r: r["created_at"], reverse=True)
    try:
        return sorted(runs, key=lambda r: r["run_id"], reverse=True)
    except TypeError as exc:
        raise ValueError(
            "supervised runs have mixed/uncomparable run_id types and no "
            "created_at to order by"
        ) from exc


def _trailing_evidence(runs: list[dict]) -> tuple[int, list[dict]]:
    """The consecutive-clean walk from most recent backwards, and exactly the
    rows it visited — void/pending rows are skipped (count for neither streak
    nor reset) but still belong in the evidence set; the walk stops at (and
    excludes) the first incident, since evidence before a streak-resetting
    incident did not contribute to this decision (design §4)."""
    streak = 0
    window: list[dict] = []
    for run in _sorted_most_recent_first(runs):
        if run["verdict"] in ("void", "pending"):
            window.append(run)
            continue
        if run["verdict"] != "clean":
            break
        streak += 1
        window.append(run)
    return streak, window


def _matches_key(run: dict, entry_id: str, version: str, model_profile: str) -> bool:
    """A run dict is scoped to this promotion's key only if it carries every
    key field and every one agrees with the key. Design §4: "the streak is
    evidence, so it does not cross the key" — a run silently missing a key
    field (a projection that dropped the columns, a hand-assembled export)
    must not be treated as pre-scoped, since that is exactly the gap that
    lets evidence from a different version/profile promote this one."""
    for field, want in (("entry_id", entry_id), ("entry_version", version),
                        ("model_profile", model_profile)):
        if run.get(field) != want:
            return False
    return True


def promote_to_validated(catalog_root: Path, *, entry_id: str, version: str,
                         model_profile: str, runs: list[dict], threshold: int,
                         run_lockbuild: Callable[[Path], None],
                         commit: Callable[[list[Path], str], None]) -> str:
    trust_path = catalog_root / "trust.yaml"
    trust_before = trust_path.read_text(encoding="utf-8") if trust_path.is_file() else None
    data = yaml.safe_load(trust_before) if trust_before else {}
    records = list((data or {}).get("records") or [])

    tier = effective_tier(records, entry_id, version, model_profile)
    if tier != "quarantined":
        raise ValueError(
            f"{entry_id} {version}@{model_profile} is {tier}, not quarantined — "
            "supervised promotion requires quarantined -> validated, no other path")

    scoped = [r for r in runs if _matches_key(r, entry_id, version, model_profile)]
    streak, evidence = _trailing_evidence(scoped)
    if streak < threshold:
        raise ValueError(
            f"{entry_id} has {streak} clean runs, needs {threshold} — not eligible")

    digest = evidence_digest(evidence)
    lock_path = catalog_root / "catalog.lock.yaml"
    lock_before = lock_path.read_text(encoding="utf-8") if lock_path.is_file() else None

    def rollback() -> None:
        if trust_before is not None:
            trust_path.write_text(trust_before, encoding="utf-8")
        elif trust_path.exists():
            trust_path.unlink()
        if lock_before is not None:
            lock_path.write_text(lock_before, encoding="utf-8")
        elif lock_path.exists():
            lock_path.unlink()

    try:
        records.append({
            "id": entry_id,
            "version": version,
            "model_profile": model_profile,
            "tier": "validated",
            "granted_by": f"supervised-{threshold}",
            "evidence": f"supervised:{digest}",
        })
        data = dict(data or {}, records=records)
        trust_path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
        run_lockbuild(catalog_root)
        commit([trust_path, lock_path],
              f"certify: promote {entry_id} to validated ({threshold} supervised runs)")
    except Exception as exc:
        rollback()
        raise RuntimeError(f"promotion of {entry_id} rolled back: {exc}") from exc

    return digest
