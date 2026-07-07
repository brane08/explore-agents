"""
skills_kit — shared skills-directory loader.

Parses ``<dir>/<tool>/SKILL.md`` folders (YAML frontmatter + prose) and an
aggregated ``manifest.json`` into plain tool dicts:

    {"name": ..., "description": ..., "input_schema": {...}}

Both agent_app and b2a_app consume these dicts and adapt them to their own
tool models (ToolSpec / ToolSchema). Keeping the loader model-neutral means one
implementation, no duplication.

Format & regeneration live with each app's thin wrapper; the authoring source
is always the SKILL.md folders — manifest.json is a generated fast path.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

_DELIM = "---"
MANIFEST = "manifest.json"

__all__ = [
    "MANIFEST",
    "SkillFormatError",
    "load_tool_dicts",
    "load_dicts_from_manifest",
    "build_manifest",
    "write_manifest",
]


class SkillFormatError(ValueError):
    """Raised when a SKILL.md is missing frontmatter or a required field."""


def _parse_skill_md(text: str, source: Path) -> dict:
    stripped = text.lstrip()
    if not stripped.startswith(_DELIM):
        raise SkillFormatError(f"{source}: missing '---' YAML frontmatter")
    # stripped == "---\n<frontmatter>\n---\n<body>"  → ['', frontmatter, body]
    parts = stripped.split(_DELIM, 2)
    if len(parts) < 3:
        raise SkillFormatError(f"{source}: unterminated frontmatter")

    front = yaml.safe_load(parts[1]) or {}
    body = parts[2].strip()

    name = front.get("name")
    if not name:
        raise SkillFormatError(f"{source}: frontmatter missing 'name'")

    description = front.get("description")
    if not description:
        description = body.splitlines()[0].strip() if body else ""

    return {
        "name": name,
        "description": description,
        "input_schema": front.get("input_schema") or {},
    }


def _load_from_folders(skills_dir: Path) -> list[dict]:
    return [
        _parse_skill_md(md.read_text(encoding="utf-8"), md)
        for md in sorted(skills_dir.glob("*/SKILL.md"))
    ]


def load_dicts_from_manifest(manifest_path: Path) -> list[dict]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return list(data.get("tools", []))


def load_tool_dicts(skills_dir: Path) -> list[dict]:
    """Discover tool dicts: manifest.json fast path, else SKILL.md folders.

    Returns ``[]`` when the directory is absent or holds no skills.
    """
    if not skills_dir.is_dir():
        return []
    manifest = skills_dir / MANIFEST
    if manifest.is_file():
        return load_dicts_from_manifest(manifest)
    return _load_from_folders(skills_dir)


def build_manifest(tool_dicts: list[dict]) -> dict:
    return {"version": 1, "tools": list(tool_dicts)}


def write_manifest(skills_dir: Path) -> Path:
    """(Re)generate ``<skills_dir>/manifest.json`` from the SKILL.md folders."""
    manifest = skills_dir / MANIFEST
    manifest.write_text(
        json.dumps(build_manifest(_load_from_folders(skills_dir)), indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest
