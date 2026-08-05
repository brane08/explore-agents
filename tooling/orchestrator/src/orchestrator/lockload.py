"""Pinned-epoch lock loading (CHECKLISTS layer 3).

The router's only input is `catalog.lock.yaml` at the session's pinned
`catalog_ref` (a git SHA) — read via `git show`, never from the working tree,
so lock rebuilds and drift never shift behavior mid-session.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from lockbuild.build import LOCK_FILENAME


def current_ref(catalog_root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(catalog_root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


_CACHE: dict[tuple[str, str], dict] = {}


def load_lock_at(catalog_root: Path, catalog_ref: str) -> dict:
    """Lock content at a SHA is immutable, so it is cached per (root, ref)."""
    key = (str(catalog_root), catalog_ref)
    if key not in _CACHE:
        text = subprocess.run(
            ["git", "-C", str(catalog_root), "show", f"{catalog_ref}:{LOCK_FILENAME}"],
            check=True, capture_output=True, text=True,
        ).stdout
        _CACHE[key] = yaml.safe_load(text)
    return _CACHE[key]


def entries_by_kind(lock: dict, *kinds: str) -> list[dict]:
    return [e for e in lock.get("entries", []) if e.get("kind") in kinds]


def entry_by_id(lock: dict, entry_id: str) -> dict | None:
    return next((e for e in lock.get("entries", []) if e.get("id") == entry_id), None)
