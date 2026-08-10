"""Runtime trust checks (CATALOG §7 recall semantics).

Tier is read from the *live* trust.yaml working tree per invocation — never
from the pinned lock — so demotion (recall) takes effect at routing time,
piercing the epoch pin. The check is transitive: agent → bindings,
instance → template, composite → members.

Semantics are shared with lockbuild (lockbuild.trust): latest record per
(id, version, model_profile) wins — the ledger is append-only, so promotion
and recall both work by appending.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from lockbuild.trust import TIER_ORDER, effective_tier, has_record

__all__ = ["TIER_ORDER", "load_records", "live_tier", "transitive_tier"]


def load_records(catalog_root: Path) -> list[dict]:
    trust = catalog_root / "trust.yaml"
    if not trust.is_file():
        return []
    data = yaml.safe_load(trust.read_text(encoding="utf-8")) or {}
    return list(data.get("records") or [])


def live_tier(records: list[dict], entry_id: str, version: str, model_profile: str) -> str:
    return effective_tier(records, entry_id, version, model_profile)


def transitive_tier(records: list[dict], lock_entry: dict, lock: dict,
                    model_profile: str) -> str:
    """Effective live tier of a lock entry: min over the entry itself and
    everything it depends on (bindings, instantiating template, members)."""
    return _transitive(records, lock_entry, lock, model_profile, seen=frozenset())


def _transitive(records: list[dict], lock_entry: dict, lock: dict,
                model_profile: str, seen: frozenset) -> str:
    eid = lock_entry["id"]
    if eid in seen:                     # composite cycles are a lockbuild failure;
        return "untrusted"              # never trust one that slipped through
    seen = seen | {eid}

    own = live_tier(records, eid, lock_entry["version"], model_profile)
    tiers = [own]

    if lock_entry.get("bindings"):
        binding_tiers = [
            live_tier(records, ref["id"], ref["version"], model_profile)
            for ref in lock_entry["bindings"]
        ]
        derived = min(binding_tiers, key=TIER_ORDER.__getitem__)
        if (lock_entry.get("assembly") == "b1"
                and not has_record(records, eid, lock_entry["version"])):
            # config-only assembly with no record of its own: derived tier IS
            # its tier. An explicit record (e.g. a recall) always participates.
            tiers = [derived]
        else:
            tiers = [own, derived]

    inst = lock_entry.get("instantiated_from")
    if inst:
        tiers.append(live_tier(records, inst["id"], inst["version"], model_profile))

    for member in lock_entry.get("members", []):
        target = next(
            (e for e in lock.get("entries", []) if e.get("id") == member["id"]), None)
        if target is None:
            tiers.append("untrusted")
        else:
            tiers.append(_transitive(records, target, lock, model_profile, seen))

    return min(tiers, key=TIER_ORDER.__getitem__)
