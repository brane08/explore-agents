from __future__ import annotations

from pathlib import Path

from lockbuild.build import build_lock, render_lock

BUILT_FROM = "0" * 40
BUILT_AT = "2026-07-15T00:00:00+00:00"


def build_text(root: Path) -> str:
    return render_lock(build_lock(root, built_from=BUILT_FROM, built_at=BUILT_AT))
