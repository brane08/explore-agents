"""
skills.py — b2a adapter over the shared skills_kit loader.

skills_kit parses the SKILL.md folders / manifest.json into model-neutral
dicts; here we adapt them to b2a's ToolSchema. See skills_kit for the format.

Discovery accepts a plain skills dir (manifest.json fast path, else SKILL.md
folders) or the platform catalog root (skills/ + mirrored mcp/ snapshots);
the caller falls back to live MCP when this returns [].

Manifest regeneration applies to plain skills dirs only — the catalog root's
aggregate is catalog.lock.yaml, built by tooling/lockbuild:

    PYTHONPATH=b2a_app/src uv run python -m loop.skills <dir>
"""
from __future__ import annotations

from pathlib import Path

from skills_kit import (  # re-exported for callers/tests
    MANIFEST,
    SkillFormatError,
    _load_from_folders,
    build_manifest,
    load_tool_dicts,
    write_manifest,
)

from loop.spec import ToolSchema

__all__ = [
    "MANIFEST",
    "SkillFormatError",
    "build_manifest",
    "write_manifest",
    "load_skill_tools",
    "_load_from_folders",
]


def load_skill_tools(skills_dir: Path) -> list[ToolSchema]:
    """Discover tools from a skills directory as b2a ToolSchemas.

    Returns ``[]`` when the directory is absent or holds no skills, so the
    caller can fall back to live MCP discovery.
    """
    return [ToolSchema(**d) for d in load_tool_dicts(skills_dir)]


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m loop.skills <skills-dir>")
    target = Path(sys.argv[1])
    out = write_manifest(target)
    print(f"wrote {out} ({len(load_tool_dicts(target))} tools)")
