"""
Unit tests for the /run-loop endpoint.

The MCP server is never contacted — available_tools is patched directly
so the endpoint can be tested without a running server.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api as api_module
from api import app
from loop.spec import ToolSchema

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
