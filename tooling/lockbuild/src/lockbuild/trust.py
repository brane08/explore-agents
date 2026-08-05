"""Shared trust-ledger semantics (CATALOG §7) — the single implementation
used by lockbuild's stage-4 join and the orchestrator's live recall check.

The ledger is append-only: history is never rewritten, so the **latest**
record per (id, version, model_profile) is the current tier — promotion
appends a higher-tier record, recall appends a lower one, and both must take
effect.
"""
from __future__ import annotations

TIER_ORDER = {"untrusted": 0, "quarantined": 1, "validated": 2}


def _latest_per_profile(records: list[dict], entry_id: str, version: str) -> dict[str, str]:
    """model_profile → tier of the latest matching record (file order = time order)."""
    latest: dict[str, str] = {}
    for r in records:
        if r.get("id") == entry_id and r.get("version") == version:
            latest[r.get("model_profile")] = r.get("tier", "untrusted")
    return latest


def has_record(records: list[dict], entry_id: str, version: str) -> bool:
    return any(
        r.get("id") == entry_id and r.get("version") == version for r in records
    )


def effective_tier(
    records: list[dict], entry_id: str, version: str, model_profile: str | None
) -> str:
    """Current tier for (id, version, model_profile).

    - profile known (runtime, or an agent manifest): the latest record at
      exactly that profile — evidence is valid per pairing only.
    - profile unknown (lockbuild joining a manifest-less entry into the
      single-tier lock field): conservative min over the latest tier of each
      profile that has records. Deliberate: the lock's tier is advisory and
      security-conservative; the router's invocation gate re-checks live at
      the resolved profile.
    """
    latest = _latest_per_profile(records, entry_id, version)
    if not latest:
        return "untrusted"
    if model_profile is not None:
        return latest.get(model_profile, "untrusted")
    return min(latest.values(), key=TIER_ORDER.__getitem__)
