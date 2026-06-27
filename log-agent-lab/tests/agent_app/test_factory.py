import pytest
from orchestrator.tool_spec import ToolSpec, AgentSpec
from orchestrator.factory import match_tools, assemble_agent

_FAKE_POOL = [
    ToolSpec(name="es_search", description="Search logs", input_schema={"type": "object"}),
    ToolSpec(name="es_aggregate", description="Aggregate logs", input_schema={"type": "object"}),
    ToolSpec(name="field_stats", description="Field stats", input_schema={"type": "object"}),
]


def test_match_tools_returns_subset():
    result = match_tools(["es_search", "field_stats"], _FAKE_POOL)
    assert len(result) == 2
    assert {t.name for t in result} == {"es_search", "field_stats"}


def test_match_tools_no_match_raises_value_error():
    with pytest.raises(ValueError, match="not found in available tool pool"):
        match_tools(["es_search", "nonexistent_tool"], _FAKE_POOL)


def test_match_tools_empty_required_raises_value_error():
    with pytest.raises(ValueError, match="required_tool_names must not be empty"):
        match_tools([], _FAKE_POOL)


def test_assemble_agent_name_is_always_orch_b1():
    spec = assemble_agent(
        model="claude-sonnet-4-6",
        required_tool_names=["es_search"],
        available=_FAKE_POOL,
        task_description="Find error spikes",
    )
    assert spec.name == "orch-b1-agent"


def test_assemble_agent_includes_matched_tools():
    spec = assemble_agent(
        model="claude-sonnet-4-6",
        required_tool_names=["es_search", "field_stats"],
        available=_FAKE_POOL,
        task_description="Analyse log fields",
    )
    assert {t.name for t in spec.tools} == {"es_search", "field_stats"}


def test_assemble_agent_system_prompt_mentions_task():
    spec = assemble_agent(
        model="claude-sonnet-4-6",
        required_tool_names=["es_search"],
        available=_FAKE_POOL,
        task_description="Detect anomalies",
    )
    assert "Detect anomalies" in spec.system_prompt


def test_assemble_agent_idempotent():
    kwargs = dict(
        model="claude-sonnet-4-6",
        required_tool_names=["es_search"],
        available=_FAKE_POOL,
        task_description="Detect anomalies",
    )
    spec_a = assemble_agent(**kwargs)
    spec_b = assemble_agent(**kwargs)
    assert spec_a.model_dump() == spec_b.model_dump()


def test_assemble_agent_no_match_raises():
    with pytest.raises(ValueError):
        assemble_agent(
            model="claude-sonnet-4-6",
            required_tool_names=["missing_tool"],
            available=_FAKE_POOL,
            task_description="Whatever",
        )
