"""Lockbuild stages 1, 3, 4, 5 (stage 2 lives in refresh.py).

Same tree ⇒ byte-identical `catalog.lock.yaml`. `built_from`/`built_at` default
to the tree's HEAD commit (SHA + committer date) — deterministic for a given
checkout, injectable for tests.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from lockbuild.errors import LockbuildError
from lockbuild.hashing import entry_hash
from lockbuild.schemas import (
    BINDABLE_KINDS,
    DIR_KINDS,
    EXTERNAL_KINDS,
    KIND_MODELS,
    REQUIRED_FILES,
    AgentManifest,
    BaseEntry,
)
from lockbuild.trust import TIER_ORDER, effective_tier, has_record

LOCK_VERSION = 3
LOCK_FILENAME = "catalog.lock.yaml"
PROPOSED_DIR = "_proposed"


@dataclass
class ScannedEntry:
    entry: BaseEntry
    kind: str
    dir: Path
    raw: dict
    hash: str = ""
    manifest: AgentManifest | None = None
    stale_reason: str | None = None
    tier: str = "untrusted"


# ---------------------------------------------------------------------------
# Stage 1 — scan & validate
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise LockbuildError(f"{path}: not a YAML mapping")
    return data


def _scan(root: Path, errors: list[str]) -> dict[str, ScannedEntry]:
    tags_file = root / "tags.yaml"
    known_tags: set[str] = set()
    if tags_file.is_file():
        known_tags = set((_load_yaml(tags_file).get("tags") or []))

    index: dict[str, ScannedEntry] = {}
    for dirname, allowed_kinds in DIR_KINDS.items():
        base = root / dirname
        if not base.is_dir():
            continue
        for entry_dir in sorted(p for p in base.iterdir() if p.is_dir()):
            if entry_dir.name == PROPOSED_DIR:
                continue  # B2 candidates never reach the lock
            entry_file = entry_dir / "entry.yaml"
            if not entry_file.is_file():
                errors.append(f"{entry_dir}: missing entry.yaml")
                continue
            try:
                raw = _load_yaml(entry_file)
            except (LockbuildError, yaml.YAMLError) as exc:
                errors.append(str(exc))
                continue

            kind = raw.get("kind")
            model_cls = KIND_MODELS.get(kind)
            if model_cls is None:
                errors.append(f"{entry_file}: unknown kind '{kind}'")
                continue
            if kind not in allowed_kinds:
                errors.append(f"{entry_file}: kind '{kind}' not allowed under {dirname}/")
                continue
            try:
                entry = model_cls.model_validate(raw)
            except ValidationError as exc:
                errors.append(f"{entry_file}: {exc}")
                continue

            if entry.id in index:
                errors.append(f"{entry_file}: duplicate id '{entry.id}'")
                continue
            for tag in entry.unproposed_tags():
                if tag not in known_tags:
                    errors.append(
                        f"{entry_file}: capability_tag '{tag}' not in tags.yaml "
                        "(add it via PR or flag it proposed: true)"
                    )
            for required in REQUIRED_FILES[kind]:
                if not (entry_dir / required).is_file():
                    errors.append(f"{entry_dir}: missing {required} (hash scope incomplete)")

            scanned = ScannedEntry(entry=entry, kind=kind, dir=entry_dir, raw=raw)
            if kind in {"agent", "component"}:
                manifest_file = entry_dir / "AGENT_MANIFEST.yaml"
                if manifest_file.is_file():
                    try:
                        scanned.manifest = AgentManifest.model_validate(_load_yaml(manifest_file))
                    except (ValidationError, LockbuildError) as exc:
                        errors.append(f"{manifest_file}: {exc}")
            index[entry.id] = scanned
    return index


def _check_bindings(index: dict[str, ScannedEntry], errors: list[str]) -> None:
    for se in index.values():
        for ref in getattr(se.entry, "bindings", []):
            target = index.get(ref.id)
            if target is None or target.entry.version != ref.version:
                errors.append(
                    f"{se.entry.id}: missing referenced entry '{ref.id}' {ref.version}"
                )
            elif target.kind not in BINDABLE_KINDS:
                errors.append(
                    f"{se.entry.id}: binding '{ref.id}' references kind "
                    f"'{target.kind}' — bindings may reference only skill|mcp-tool"
                )


# ---------------------------------------------------------------------------
# Stage 4 — trust join & integrity (lazy invalidation)
# ---------------------------------------------------------------------------

def _load_trust(root: Path) -> list[dict]:
    trust_file = root / "trust.yaml"
    if not trust_file.is_file():
        return []
    return list(_load_yaml(trust_file).get("records") or [])


def _tier_for(se: ScannedEntry, records: list[dict]) -> str:
    profile = se.manifest.model_profile if se.manifest else None
    return effective_tier(records, se.entry.id, se.entry.version, profile)


def _join(index: dict[str, ScannedEntry], root: Path, errors: list[str]) -> None:
    records = _load_trust(root)

    for se in index.values():
        se.hash = entry_hash(se.dir, se.kind)

    for se in index.values():
        se.tier = _tier_for(se, records)

    # config-only B1 registrations (CATALOG §5): tier is derived from the
    # parts — min(binding tiers) — since the assembly contains zero novel code.
    # Any explicit trust record takes precedence, INCLUDING an untrusted one:
    # a recorded recall must never be re-derived back up.
    for se in index.values():
        if (getattr(se.entry, "assembly", None) == "b1"
                and not has_record(records, se.entry.id, se.entry.version)):
            binding_tiers = [
                index[ref.id].tier
                for ref in se.entry.bindings
                if ref.id in index
            ]
            if binding_tiers and len(binding_tiers) == len(se.entry.bindings):
                se.tier = min(binding_tiers, key=TIER_ORDER.__getitem__)

    for se in index.values():
        stale: list[str] = []

        if se.manifest:
            for mb in se.manifest.bindings:
                target = index.get(mb.id)
                if target is None or target.entry.version != mb.version:
                    errors.append(
                        f"{se.entry.id}: manifest references missing entry '{mb.id}' {mb.version}"
                    )
                elif target.hash != mb.schema_hash:
                    stale.append("binding-drift")
            profile = se.manifest.model_profile
            if profile is not None:
                model = index.get(profile)
                if model is None or model.kind != "model":
                    errors.append(
                        f"{se.entry.id}: model_profile '{profile}' has no models/ entry"
                    )
                elif model.raw.get("status") == "deprecated":
                    stale.append("model-drift")

        inst = getattr(se.entry, "instantiated_from", None)
        if inst is not None:
            tmpl = index.get(inst.id)
            if tmpl is None or tmpl.entry.version != inst.version:
                errors.append(
                    f"{se.entry.id}: missing referenced template '{inst.id}' {inst.version}"
                )
            elif tmpl.hash != inst.hash:
                stale.append("template-drift")

        for reason in ("binding-drift", "template-drift", "model-drift"):
            if reason in stale:
                se.stale_reason = reason
                break

    _resolve_composites(index, errors, records)


def _resolve_composites(
    index: dict[str, ScannedEntry], errors: list[str], records: list[dict]
) -> None:
    resolving: set[str] = set()
    done: set[str] = set()

    def tier_of(eid: str, chain: tuple[str, ...]) -> str:
        se = index.get(eid)
        if se is None:
            return "untrusted"
        if se.kind != "composite" or eid in done:
            return se.tier
        if eid in resolving:
            raise LockbuildError(
                f"composite cycle detected: {' -> '.join(chain + (eid,))}"
            )
        resolving.add(eid)
        member_tiers = []
        for ref in se.entry.members:
            target = index.get(ref.id)
            if target is None or target.entry.version != ref.version:
                errors.append(
                    f"{eid}: missing referenced member '{ref.id}' {ref.version}"
                )
                member_tiers.append("untrusted")
            else:
                member_tiers.append(tier_of(ref.id, chain + (eid,)))
        derived = min(member_tiers or ["untrusted"], key=TIER_ORDER.__getitem__)
        # Any explicit trust record takes precedence, INCLUDING an untrusted
        # one: a recorded recall must never be re-derived back up (same
        # invariant as the B1 assembly derivation above).
        if not has_record(records, se.entry.id, se.entry.version):
            se.tier = derived
        resolving.discard(eid)
        done.add(eid)
        return se.tier

    for eid, se in index.items():
        if se.kind == "composite":
            tier_of(eid, ())


# ---------------------------------------------------------------------------
# Stage 5 — emit
# ---------------------------------------------------------------------------

def _routing_summary(se: ScannedEntry) -> str:
    if se.kind in EXTERNAL_KINDS:
        return (se.dir / "routing_summary.md").read_text(encoding="utf-8").strip()
    return se.entry.routing_summary or ""


def _lock_entry(se: ScannedEntry) -> dict:
    out: dict = {
        "id": se.entry.id,
        "kind": se.kind,
        "version": se.entry.version,
        "hash": se.hash,
        "trust_tier": se.tier,
        "routing_summary": _routing_summary(se),
        "capability_tags": se.entry.tag_names(),
        "detail": se.entry.detail,
    }
    if se.stale_reason:
        out["stale"] = True
        out["stale_reason"] = se.stale_reason
    for opt in ("bindings", "slots", "instantiated_from",
                "model_requirements", "delegation_requirements", "assembly"):
        if se.raw.get(opt):
            out[opt] = se.raw[opt]
    return out


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def build_lock(root: Path, built_from: str | None = None, built_at: str | None = None) -> dict:
    root = Path(root)
    if built_from is None:
        built_from = _git(root, "rev-parse", "HEAD")
    if built_at is None:
        built_at = _git(root, "show", "-s", "--format=%cI", "HEAD")

    errors: list[str] = []
    index = _scan(root, errors)
    if errors:
        raise LockbuildError("\n".join(errors))
    _check_bindings(index, errors)
    if errors:
        raise LockbuildError("\n".join(errors))
    _join(index, root, errors)
    if errors:
        raise LockbuildError("\n".join(errors))

    return {
        "lock_version": LOCK_VERSION,
        "built_from": built_from,
        "built_at": built_at,
        "entries": [_lock_entry(index[eid]) for eid in sorted(index)],
    }


def render_lock(lock: dict) -> str:
    header = (
        "# GENERATED by tooling/lockbuild — never hand-edited.\n"
        "# Router input. Rebuild: `lockbuild build`. Verify: `lockbuild verify`.\n"
    )
    return header + yaml.safe_dump(
        lock, sort_keys=True, allow_unicode=True, default_flow_style=False, width=100
    )


def verify_lock(
    root: Path,
    lock_path: Path | None = None,
    built_from: str | None = None,
    built_at: str | None = None,
) -> bool:
    root = Path(root)
    lock_path = lock_path or root / LOCK_FILENAME
    if not lock_path.is_file():
        return False
    committed = lock_path.read_text(encoding="utf-8")
    if built_from is None or built_at is None:
        # provenance is taken from the committed lock (it records the tree it
        # was built at); verify gates *content* equality against the tree
        head = yaml.safe_load(committed)
        if not isinstance(head, dict):
            return False
        built_from = built_from or head.get("built_from")
        built_at = built_at or head.get("built_at")
    expected = render_lock(build_lock(root, built_from=built_from, built_at=built_at))
    return committed == expected
