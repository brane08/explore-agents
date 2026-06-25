import pytest
from agent_app.orchestrator.tool_spec import AgentSpec, ToolSpec
from agent_app.orchestrator.registry import AgentRegistry

_SPEC = AgentSpec(
    name="orch-b1-agent",
    model="claude-sonnet-4-6",
    tools=(ToolSpec(name="es_search", description="Search", input_schema={}),),
    system_prompt="You are a log agent.",
)


def test_register_and_get():
    reg = AgentRegistry()
    reg.register(_SPEC)
    assert reg.get("orch-b1-agent") == _SPEC


def test_get_unknown_returns_none():
    reg = AgentRegistry()
    assert reg.get("no-such-agent") is None


def test_list_names_empty():
    reg = AgentRegistry()
    assert reg.list_names() == []


def test_list_names_after_register():
    reg = AgentRegistry()
    reg.register(_SPEC)
    assert "orch-b1-agent" in reg.list_names()


def test_register_overwrites():
    reg = AgentRegistry()
    reg.register(_SPEC)
    updated = AgentSpec(
        name="orch-b1-agent",
        model="claude-opus-4-8",
        tools=_SPEC.tools,
        system_prompt="Updated prompt.",
    )
    reg.register(updated)
    assert reg.get("orch-b1-agent").model == "claude-opus-4-8"
