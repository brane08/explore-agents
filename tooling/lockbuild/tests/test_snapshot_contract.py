"""The mirrored snapshots still describe what the tool plane actually serves.

`mcp-tool` is an external kind: the catalog carries a `registry_url` and a
mirrored `schema.snapshot.json`, and CATALOG §3 makes the schema — not the
prose — the contract boundary. Mirroring means the catalog's copy can go stale
without anything failing: lockbuild hashes the snapshot it finds, routing scores
the summary derived from it, and B1 assembles tools against it. Every one of
those steps succeeds against a schema the server stopped honouring three commits
ago, and the first symptom is a tool call rejected at runtime.

Refresh (CATALOG §5 stage 2) is the mechanism that repairs drift; this is the
gate that notices it. Skipped when the reference server isn't importable, so the
default `pytest tooling` run stays dependency-free — CI installs the extra and
runs it for real (see .github/workflows/lockbuild.yml).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[3]
TOOL_PLANE = ROOT / "reference-tools" / "src"

# A gate that skips itself is not a gate. Locally the default run stays
# dependency-free, but CI sets SNAPSHOT_CONTRACT_REQUIRED so a missing extra or
# a moved tool plane fails loudly instead of quietly passing.
_REQUIRED = os.environ.get("SNAPSHOT_CONTRACT_REQUIRED") == "1"


def _unavailable() -> str:
    if importlib.util.find_spec("fastmcp") is None:
        return "fastmcp is not installed (orchestrator[mcp])"
    if not TOOL_PLANE.is_dir():
        return f"reference tool plane not found at {TOOL_PLANE}"
    return ""


if _reason := _unavailable():
    if _REQUIRED:
        raise RuntimeError(
            f"SNAPSHOT_CONTRACT_REQUIRED=1 but the contract cannot run: {_reason}"
        )
    pytest.skip(_reason, allow_module_level=True)


@pytest.fixture(scope="module")
def served() -> dict[str, dict]:
    """`{tool_name: {name, description, inputSchema}}` straight from the server.

    In-memory transport: no port, no subprocess, no network — the schemas come
    from the same code path that would answer a real `list_tools` call.
    """
    import anyio
    import fastmcp

    if str(TOOL_PLANE) not in sys.path:
        sys.path.insert(0, str(TOOL_PLANE))
    from server import mcp  # noqa: PLC0415 - path is only valid after the insert

    async def collect() -> dict[str, dict]:
        async with fastmcp.Client(mcp) as client:
            return {
                t.name: {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.inputSchema,
                }
                for t in await client.list_tools()
            }

    return anyio.run(collect)


def _type_of(body: dict) -> str:
    """The declared type, with optionality normalized away.

    A server-generated schema renders `Optional[str]` as
    `anyOf: [{type: string}, {type: null}]`; a hand-authored SKILL.md writes
    `type: string` and expresses optionality by omitting the field from
    `required`. Both say the same thing, so compare the non-null type and let
    `required` carry optionality on its own.
    """
    if "type" in body:
        return str(body["type"])
    variants = body.get("anyOf") or body.get("oneOf") or []
    types = sorted({v.get("type", "") for v in variants if v.get("type") != "null"})
    return "|".join(types)


def _properties(schema: dict) -> dict[str, str]:
    """Property name → declared type. Prose (descriptions, defaults) is
    deliberately excluded: a SKILL.md may document a capability more richly than
    the generated schema does, and that is not drift."""
    return {
        name: _type_of(body)
        for name, body in (schema or {}).get("properties", {}).items()
    }


def _required(schema: dict) -> set[str]:
    return set((schema or {}).get("required", []))


def test_the_reference_plane_serves_the_tools_the_catalog_binds():
    """Guards the move of the tool plane out of log-agent-lab: if the server is
    gone or renamed, every other assertion here would vacuously pass."""
    entries = {p.parent.name for p in ROOT.glob("mcp/*/entry.yaml")}
    entries |= {p.parent.name for p in ROOT.glob("skills/*/entry.yaml")}
    assert entries, "no catalog entries found — wrong ROOT?"


def test_mirrored_mcp_snapshots_match_the_live_schema(served):
    """`mcp/<id>/schema.snapshot.json` is a mirror, so equality is the contract —
    any difference means the snapshot needs a refresh commit."""
    snapshots = sorted(ROOT.glob("mcp/*/schema.snapshot.json"))
    assert snapshots, "no mcp snapshots in the catalog"
    for path in snapshots:
        tool_id = path.parent.name
        assert tool_id in served, (
            f"{tool_id} is catalogued at {path.parent} but the tool plane no "
            f"longer serves it; served: {sorted(served)}"
        )
        mirrored = json.loads(path.read_text(encoding="utf-8"))
        assert mirrored == served[tool_id], (
            f"{tool_id} snapshot has drifted from the served schema — "
            f"regenerate with a refresh commit"
        )


def test_skill_frontmatter_matches_the_tool_it_calls(served):
    """Skills reach the same server through `impl/run.py`, so their declared
    `input_schema` is a second, hand-authored copy of the same contract."""
    checked = 0
    for skill_md in sorted(ROOT.glob("skills/*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        _, _, rest = text.partition("---")
        front, _, _ = rest.partition("---")
        meta = yaml.safe_load(front) or {}
        tool_id = meta.get("name", skill_md.parent.name)
        if tool_id not in served:
            continue  # a skill that does not front an MCP tool
        declared, live = meta.get("input_schema", {}), served[tool_id]["inputSchema"]
        assert _properties(declared) == _properties(live), (
            f"{skill_md}: declares {_properties(declared)}, server serves "
            f"{_properties(live)}"
        )
        assert _required(declared) == _required(live), (
            f"{skill_md}: required set differs from the served schema"
        )
        checked += 1
    assert checked, "no skill fronted an MCP tool — contract unverified"
