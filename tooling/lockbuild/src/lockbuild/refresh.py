"""Stage 2 — external refresh (`--refresh` only; scheduled job).

Fetches external schemas/cards via an injected `fetch` callable, normalizes
(sorted keys, volatile fields stripped), rewrites the snapshot on change, and
regenerates `routing_summary.md` **from the schema only** — vendor prose never
becomes the routing summary (tool-description-poisoning control, CATALOG §4).

Refresh output must land on a PR branch, never `main`: refusal is enforced here.
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import yaml

VOLATILE_PREFIXES = ("x-",)
VOLATILE_KEYS = {"$comment"}

SNAPSHOT_FILES = {"mcp": "schema.snapshot.json", "a2a": "agentcard.snapshot.json"}


class RefreshOnMainError(Exception):
    """--refresh outputs must land on a PR branch, never main."""


def normalize(obj: object) -> object:
    if isinstance(obj, dict):
        return {
            k: normalize(v)
            for k, v in obj.items()
            if k not in VOLATILE_KEYS and not k.startswith(VOLATILE_PREFIXES)
        }
    if isinstance(obj, list):
        return [normalize(v) for v in obj]
    return obj


def render_snapshot(schema: dict) -> str:
    return json.dumps(normalize(schema), indent=2, sort_keys=True) + "\n"


def summarize_schema(schema: dict) -> str:
    """Deterministic extractive summary — built from structure, never from the
    vendor description. An LLM regeneration step can replace this later; the
    invariant (schema-derived only) must hold either way."""
    name = schema.get("name", "unknown")
    props = sorted((schema.get("inputSchema") or {}).get("properties") or {})
    params = f" Parameters: {', '.join(props)}." if props else ""
    return f"Mirrored MCP tool {name}.{params} Summary regenerated from schema snapshot.\n"


def _current_branch(root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "branch", "--show-current"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def refresh(
    root: Path,
    fetch: Callable[[dict], dict],
    summarize: Callable[[dict], str] = summarize_schema,
) -> list[str]:
    """Refresh every external entry; returns the ids whose snapshot changed."""
    root = Path(root)
    if _current_branch(root) == "main":
        raise RefreshOnMainError("refresh outputs must land on a PR branch, never main")

    changed: list[str] = []
    for dirname, snapshot_name in SNAPSHOT_FILES.items():
        base = root / dirname
        if not base.is_dir():
            continue
        for entry_dir in sorted(p for p in base.iterdir() if p.is_dir()):
            entry_file = entry_dir / "entry.yaml"
            if not entry_file.is_file():
                continue
            entry = yaml.safe_load(entry_file.read_text(encoding="utf-8"))
            new_text = render_snapshot(fetch(entry))
            snapshot = entry_dir / snapshot_name
            old_text = snapshot.read_text(encoding="utf-8") if snapshot.is_file() else None
            if new_text == old_text:
                continue
            snapshot.write_text(new_text, encoding="utf-8")
            (entry_dir / "routing_summary.md").write_text(
                summarize(json.loads(new_text)), encoding="utf-8"
            )
            changed.append(entry["id"])
    return changed
