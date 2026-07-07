"""
skills.py — agent_app adapter over the shared skills_kit loader.

skills_kit parses the SKILL.md folders / manifest.json into model-neutral
dicts; here we adapt them to agent_app's ToolSpec. Both services share one
skills catalog (the repo-level skills/ directory) this way.
"""
from __future__ import annotations

from pathlib import Path

from skills_kit import load_tool_dicts

from orchestrator.tool_spec import ToolSpec


def load_skill_tools(skills_dir: Path) -> list[ToolSpec]:
    """Discover tools from a skills directory as agent_app ToolSpecs.

    Returns ``[]`` when the directory is absent or holds no skills, so the
    caller can fall back to live MCP discovery.
    """
    return [ToolSpec(**d) for d in load_tool_dicts(skills_dir)]
