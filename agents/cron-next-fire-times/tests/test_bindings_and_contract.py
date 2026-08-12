"""Binding audit + skeleton/slot discipline.

This candidate declares zero tool bindings (§3.1): the capability is pure
computation and nothing in CAPABILITY_MANIFEST covers cron scheduling. The
"negative test per binding" therefore degenerates to proving the binding set is
empty and that no agent-kind entry is invoked from src/.
"""

import os
import re

from config import AgentConfig

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")


def _manifest():
    """Load the manifest with PyYAML when present, else a minimal fallback parse."""
    path = os.path.join(ROOT, "AGENT_MANIFEST.yaml")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    try:
        import yaml
    except ImportError:
        pass
    else:
        return yaml.safe_load(text)

    bindings = re.search(r"^bindings:\s*(\[\s*\])?\s*$", text, re.M)
    names = re.findall(r"^\s*-\s*name:\s*(\S+)\s*$", text, re.M)
    return {
        "bindings": [] if bindings else None,
        "slot_value_surface": [{"name": name} for name in names],
    }


def _src_text():
    chunks = []
    for name in sorted(os.listdir(SRC)):
        if name.endswith(".py"):
            with open(os.path.join(SRC, name), encoding="utf-8") as handle:
                chunks.append(handle.read())
    return "\n".join(chunks)


def test_manifest_declares_no_bindings_and_config_agrees():
    manifest = _manifest()
    assert manifest.get("bindings") in (None, [], {})
    assert AgentConfig().bindings == []


def test_no_agent_kind_invocation_in_src():
    text = _src_text()
    for forbidden in ("a2a", "es_search", "es_aggregate", "field_stats", "b1-8ef03559ba22"):
        assert forbidden not in text
    assert not re.search(r"kind:\s*(agent|component|a2a-agent)", text)


def test_no_hardcoded_model_identifiers_or_secrets_in_src():
    text = _src_text().lower()
    for forbidden in ("claude-", "gpt-4", "gemini-", "api_key", "secret", "authorization:"):
        assert forbidden not in text


def test_slot_values_are_configuration_not_skeleton_constants():
    with open(os.path.join(SRC, "agent.py"), encoding="utf-8") as handle:
        agent_source = handle.read()
    # The count of fire times is a slot value, never a literal in the skeleton.
    assert "fire_time_count" in agent_source
    assert "= 5" not in agent_source

    manifest = _manifest()
    surface = {entry["name"] for entry in manifest["slot_value_surface"]}
    assert {"fire_time_count", "output_timezone", "prompt_path"} <= surface


def test_graph_terminates_within_the_iteration_budget():
    from agent import run_state

    state = run_state(
        {
            "cron_expression": "*/10 * * * *",
            "start_timestamp": "2026-08-12T00:00:00Z",
            "config": AgentConfig(),
            "iterations": 0,
        }
    )
    assert state["iterations"] <= AgentConfig().max_node_iterations
    assert state["result"]["status"] == "ok"
