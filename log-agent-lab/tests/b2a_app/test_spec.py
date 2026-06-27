import os
import pytest
from loop.spec import TaskSpec, ToolSchema

_TOOL = ToolSchema(name="es_search", description="Search logs", input_schema={"type": "object"})

_BASE = dict(
    task_description="Analyse error rate",
    mcp_server_url="http://localhost:8000",
    available_tools=[_TOOL],
    eval_case_ids=["search_error_level"],
)


def test_default_agent_name():
    spec = TaskSpec(**_BASE)
    assert spec.agent_name == "orch-b2a-agent"


def test_default_max_iterations():
    spec = TaskSpec(**_BASE)
    assert spec.max_iterations == 5


def test_max_iterations_from_env(monkeypatch):
    monkeypatch.setenv("B2A_MAX_ITERATIONS", "3")
    spec = TaskSpec(**_BASE)
    assert spec.max_iterations == 3


def test_spec_is_frozen():
    spec = TaskSpec(**_BASE)
    with pytest.raises(Exception):
        spec.agent_name = "other"


def test_tool_schema_is_frozen():
    with pytest.raises(Exception):
        _TOOL.name = "other"


def test_available_tools_preserved():
    spec = TaskSpec(**_BASE)
    assert spec.available_tools[0].name == "es_search"
