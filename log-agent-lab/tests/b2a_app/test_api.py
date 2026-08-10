"""
Unit tests for the /run-loop endpoint.

The MCP server is never contacted — available_tools is patched directly
so the endpoint can be tested without a running server.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from loop.spec import ToolSchema

# Load b2a's api.py under a unique name: agent_app also has a top-level `api`,
# so a plain `import api` collides across apps in a single test session. The
# module must be registered in sys.modules so pydantic can resolve the models'
# forward references (api.py uses `from __future__ import annotations`).
_spec = importlib.util.spec_from_file_location(
    "b2a_api", Path(__file__).parents[2] / "b2a_app" / "src" / "api.py"
)
api_module = importlib.util.module_from_spec(_spec)
sys.modules["b2a_api"] = api_module
_spec.loader.exec_module(api_module)
app = api_module.app

_STUB_TOOLS = [
    ToolSchema(name="es_search", description="Search logs", input_schema={}),
    ToolSchema(name="es_aggregate", description="Aggregate logs", input_schema={}),
    ToolSchema(name="field_stats", description="Field stats", input_schema={}),
]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(api_module, "_available_tools", list(_STUB_TOOLS))
    return TestClient(app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["tools_loaded"] == 3


def test_list_tools(client):
    r = client.get("/tools")
    assert r.status_code == 200
    tools = r.json()["tools"]
    assert {t["name"] for t in tools} == {"es_search", "es_aggregate", "field_stats"}
    assert all({"name", "description", "input_schema"} <= t.keys() for t in tools)


def test_run_loop_returns_report(client):
    r = client.post("/run-loop", json={
        "task_description": "test task",
        "eval_case_ids": ["search_error_level", "aggregate_by_service"],
        "max_iterations": 1,
        "sandbox_timeout": 10,
    })
    assert r.status_code == 200
    body = r.json()
    assert "stopped_reason" in body
    assert "final_score" in body
    assert isinstance(body["trend"], list)
    assert isinstance(body["case_results"], list)


def test_run_loop_unknown_case_id(client):
    r = client.post("/run-loop", json={"eval_case_ids": ["nonexistent_case"]})
    assert r.status_code == 422
    assert "nonexistent_case" in r.json()["detail"]


def test_run_loop_no_tools(monkeypatch):
    monkeypatch.setattr(api_module, "_available_tools", [])
    c = TestClient(app)
    r = c.post("/run-loop", json={})
    assert r.status_code == 503


def test_run_reasoning_unknown_task_id(client):
    r = client.post("/run-reasoning", json={"task_ids": ["nope"]})
    assert r.status_code == 422
    assert "nope" in r.json()["detail"]


def test_run_reasoning_requires_provider(client, monkeypatch):
    # no LLM provider configured -> 503 (reasoning can't run on the stub)
    monkeypatch.setattr(api_module, "_completer_factory", lambda: None)
    r = client.post("/run-reasoning", json={})
    assert r.status_code == 503
    assert "provider" in r.json()["detail"].lower()


def test_run_reasoning_no_tools(monkeypatch):
    monkeypatch.setattr(api_module, "_available_tools", [])
    c = TestClient(app)
    r = c.post("/run-reasoning", json={})
    assert r.status_code == 503
