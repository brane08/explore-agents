"""Stage 3 — kind-scoped content hashes.

Deterministic by construction: sorted POSIX relative paths, path names in the
hash input, LF-normalized bytes, no mtimes.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from lockbuild.schemas import HASH_SCOPES


def _scope_files(entry_dir: Path, kind: str) -> list[Path]:
    files: set[Path] = set()
    for pattern in HASH_SCOPES[kind]:
        if pattern.endswith("/**"):
            sub = entry_dir / pattern[:-3]
            if sub.is_dir():
                files.update(p for p in sub.rglob("*") if p.is_file())
        else:
            p = entry_dir / pattern
            if p.is_file():
                files.add(p)
    return sorted(files, key=lambda p: p.relative_to(entry_dir).as_posix())


def entry_hash(entry_dir: Path, kind: str) -> str:
    h = hashlib.sha256()
    for p in _scope_files(entry_dir, kind):
        rel = p.relative_to(entry_dir).as_posix().encode("utf-8")
        data = p.read_bytes().replace(b"\r\n", b"\n")
        # length-prefixed fields: content containing NUL/newlines can never
        # forge a file boundary, so distinct trees cannot collide
        h.update(f"{len(rel)}:".encode())
        h.update(rel)
        h.update(f"{len(data)}:".encode())
        h.update(data)
    return h.hexdigest()
