"""
Tests for the skills-directory tool loader (loop/skills.py).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval_suite.log_analysis_cases import CASES
from loop.skills import (
    MANIFEST,
    SkillFormatError,
    _load_from_folders,
    build_manifest,
    load_skill_tools,
)

_REPO_SKILLS = Path(__file__).parents[2] / "skills"

_SKILL_MD = """\
---
name: my_tool
description: A short description.
input_schema:
  type: object
  properties:
    q:
      type: string
  required:
    - q
---
Prose usage notes here.
"""


def _write_skill(root: Path, folder: str, text: str) -> None:
    d = root / folder
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(text, encoding="utf-8")


def test_loads_one_skill(tmp_path):
    _write_skill(tmp_path, "my_tool", _SKILL_MD)
    tools = load_skill_tools(tmp_path)
    assert len(tools) == 1
    t = tools[0]
    assert t.name == "my_tool"
    assert t.description == "A short description."
    assert t.input_schema["required"] == ["q"]


def test_missing_dir_returns_empty(tmp_path):
    assert load_skill_tools(tmp_path / "nope") == []


def test_empty_dir_returns_empty(tmp_path):
    assert load_skill_tools(tmp_path) == []


def test_description_falls_back_to_body(tmp_path):
    text = "---\nname: t\ninput_schema: {type: object}\n---\nFirst body line.\nSecond.\n"
    _write_skill(tmp_path, "t", text)
    assert load_skill_tools(tmp_path)[0].description == "First body line."


def test_missing_frontmatter_raises(tmp_path):
    _write_skill(tmp_path, "bad", "no frontmatter here\n")
    with pytest.raises(SkillFormatError, match="frontmatter"):
        load_skill_tools(tmp_path)


def test_missing_name_raises(tmp_path):
    _write_skill(tmp_path, "bad", "---\ndescription: x\n---\nbody\n")
    with pytest.raises(SkillFormatError, match="name"):
        load_skill_tools(tmp_path)


# --- the repo's real skills directory is the source of truth for the loop ---

def test_repo_skills_cover_every_eval_case():
    tools = load_skill_tools(_REPO_SKILLS)
    names = {t.name for t in tools}
    assert names == {"es_search", "es_aggregate", "field_stats"}
    # every eval case's tool has a skill definition
    for case in CASES:
        assert case.tool_name in names


def test_repo_manifest_in_sync_with_folders():
    """Guard against a stale committed manifest.json — regenerate with
    `python -m loop.skills` if this fails."""
    committed = json.loads((_REPO_SKILLS / MANIFEST).read_text(encoding="utf-8"))
    rebuilt = build_manifest(_load_from_folders(_REPO_SKILLS))
    assert committed == rebuilt


def test_load_prefers_manifest_over_folders(tmp_path):
    _write_skill(tmp_path, "my_tool", _SKILL_MD)  # folder defines my_tool
    (tmp_path / MANIFEST).write_text(
        json.dumps({"version": 1, "tools": [
            {"name": "from_manifest", "description": "m", "input_schema": {}}
        ]}),
        encoding="utf-8",
    )
    assert [t.name for t in load_skill_tools(tmp_path)] == ["from_manifest"]
