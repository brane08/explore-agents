"""Programmatic catalog fixtures.

`make_catalog(tmp_path)` builds a minimal, valid catalog tree covering every
entry kind lockbuild must handle; tests mutate the tree to provoke failures,
stale flags, and trust joins. Deterministic content only — no timestamps.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

BUILT_FROM = "0" * 40
BUILT_AT = "2026-07-15T00:00:00+00:00"

TAGS = ["log-source", "log-analysis", "read-only", "report-sink"]


def w(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def wyaml(path: Path, obj: object) -> Path:
    return w(path, yaml.safe_dump(obj, sort_keys=True))


def skill(root: Path, sid: str, tags: list | None = None) -> None:
    wyaml(root / "skills" / sid / "entry.yaml", {
        "id": sid,
        "kind": "skill",
        "version": "1.0.0",
        "routing_summary": f"The {sid} skill.",
        "capability_tags": tags if tags is not None else ["read-only"],
        "detail": f"Details about {sid}.",
    })
    w(root / "skills" / sid / "SKILL.md", f"---\nname: {sid}\n---\nBody of {sid}.\n")
    w(root / "skills" / sid / "impl" / "run.py", f"print({sid!r})\n")


def mcp_tool(root: Path, tid: str) -> None:
    wyaml(root / "mcp" / tid / "entry.yaml", {
        "id": tid,
        "kind": "mcp-tool",
        "version": "1.0.0",
        "registry_url": f"http://localhost:8000/{tid}",
        "routing_summary_provenance": "regenerated",
        "capability_tags": ["log-source"],
        "detail": f"Details about {tid}.",
    })
    w(root / "mcp" / tid / "schema.snapshot.json",
      '{\n  "inputSchema": {\n    "type": "object"\n  },\n  "name": "%s"\n}\n' % tid)
    w(root / "mcp" / tid / "routing_summary.md", f"Mirrored MCP tool {tid}.\n")


def model(root: Path, mid: str, status: str = "active") -> None:
    wyaml(root / "models" / mid / "entry.yaml", {
        "id": mid,
        "kind": "model",
        "version": "1.0.0",
        "routing_summary": f"Model profile {mid}.",
        "provider": "anthropic",
        "endpoint_class": "anthropic",
        "capabilities": {"tool_use": True, "context_window": 200000},
        "cost_class": "high",
        "profile_class": "opus-class",
        "status": status,
    })


def template(root: Path, tid: str) -> None:
    wyaml(root / "templates" / tid / "entry.yaml", {
        "id": tid,
        "kind": "template",
        "version": "1.0.0",
        "routing_summary": f"Template {tid}.",
        "capability_tags": ["report-sink"],
        "slots": [
            {"name": "source", "type": "binding", "required": True,
             "accepts": {"capability_tags": ["log-source"], "max_tier_required": "validated"}},
            {"name": "fmt", "type": "enum", "required": False, "accepts": ["text", "html"]},
        ],
        "model_requirements": {"tool_use": True},
    })
    w(root / "templates" / tid / "impl" / "skeleton.py", "SKELETON = True\n")
    w(root / "templates" / tid / "prompts" / "default.md", "Do the task.\n")


def agent(root: Path, aid: str, bindings: list[dict], model_profile: str,
          manifest_bindings: list[dict], instantiated_from: dict | None = None) -> None:
    entry = {
        "id": aid,
        "kind": "agent",
        "version": "1.0.0",
        "routing_summary": f"Agent {aid}.",
        "capability_tags": ["log-analysis"],
        "bindings": bindings,
        "model_requirements": {"tool_use": True},
    }
    if instantiated_from:
        entry["instantiated_from"] = instantiated_from
    wyaml(root / "agents" / aid / "entry.yaml", entry)
    wyaml(root / "agents" / aid / "AGENT_MANIFEST.yaml", {
        "model_profile": model_profile,
        "bindings": manifest_bindings,
    })
    w(root / "agents" / aid / "src" / "main.py", f"AGENT = {aid!r}\n")
    w(root / "agents" / aid / "prompts" / "default.md", f"You are {aid}.\n")


def composite(root: Path, cid: str, members: list[dict]) -> None:
    wyaml(root / "agents" / cid / "entry.yaml", {
        "id": cid,
        "kind": "composite",
        "version": "1.0.0",
        "routing_summary": f"Composite {cid}.",
        "members": members,
    })


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    """Minimal valid catalog: 2 skills, 1 mcp-tool, 1 model, 1 template,
    1 agent (bound to a skill + the mcp-tool), 1 template instance, 1 composite."""
    root = tmp_path / "catalog"
    wyaml(root / "tags.yaml", {"tags": TAGS})
    wyaml(root / "trust.yaml", {"records": []})

    skill(root, "greet", tags=["read-only", "log-analysis"])
    skill(root, "farewell")
    mcp_tool(root, "echo-tool")
    model(root, "opus-class-ref")
    template(root, "report-template")

    # hashes recorded in manifests must match the tree at fixture-build time
    from lockbuild.hashing import entry_hash
    greet_h = entry_hash(root / "skills" / "greet", "skill")
    echo_h = entry_hash(root / "mcp" / "echo-tool", "mcp-tool")
    tmpl_h = entry_hash(root / "templates" / "report-template", "template")

    agent(
        root, "composed",
        bindings=[{"id": "greet", "version": "1.0.0"},
                  {"id": "echo-tool", "version": "1.0.0"}],
        model_profile="opus-class-ref",
        manifest_bindings=[
            {"id": "greet", "version": "1.0.0", "hash": greet_h},
            {"id": "echo-tool", "version": "1.0.0", "hash": echo_h},
        ],
    )
    agent(
        root, "report-instance",
        bindings=[{"id": "greet", "version": "1.0.0"}],
        model_profile="opus-class-ref",
        manifest_bindings=[{"id": "greet", "version": "1.0.0", "hash": greet_h}],
        instantiated_from={"id": "report-template", "version": "1.0.0", "hash": tmpl_h},
    )
    composite(root, "pair", members=[
        {"id": "greet", "version": "1.0.0"},
        {"id": "farewell", "version": "1.0.0"},
    ])
    # a proposed candidate that must never reach the lock
    wyaml(root / "agents" / "_proposed" / "candidate-x" / "entry.yaml", {
        "id": "candidate-x", "kind": "agent", "version": "0.1.0",
        "routing_summary": "Should never appear.",
    })
    return root


def edit_entry(root: Path, rel: str, **updates) -> None:
    """Load <root>/<rel>/entry.yaml, apply updates (None deletes), rewrite."""
    p = root / rel / "entry.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    for k, v in updates.items():
        if v is None:
            data.pop(k, None)
        else:
            data[k] = v
    wyaml(p, data)
