"""
Tests for agent_app's HTTP surface with skills-first tool discovery.

No MCP server runs here — the shared skills catalog populates the tool pool,
proving discovery no longer depends on a live registry. (Runtime tool calls in
/chat still target MCP; they error harmlessly without one, which is fine for
asserting agent assembly.)
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Load agent_app's api.py under a unique name: b2a_app also has a top-level
# `api`, so a plain `import api` collides across apps in a single test session.
# Register in sys.modules so pydantic can resolve model forward references.
_spec = importlib.util.spec_from_file_location(
    "agent_api", Path(__file__).parents[2] / "agent_app" / "src" / "api.py"
)
_api = importlib.util.module_from_spec(_spec)
sys.modules["agent_api"] = _api
_spec.loader.exec_module(_api)
app = _api.app


@pytest.fixture()
def client():
    with TestClient(app) as c:  # runs lifespan → skills discovery
        yield c


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_list_tools_from_skills(client):
    tools = client.get("/tools").json()["tools"]
    assert {t["name"] for t in tools} == {"es_search", "es_aggregate", "field_stats"}


def test_chat_assembles_agent_from_skills(client):
    r = client.post("/chat", json={
        "task_description": "find errors",
        "message": "go",
        "required_tools": ["es_search"],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tools_used"] == ["es_search"]
    assert body["agent_name"]


def test_chat_rejects_unknown_tool(client):
    r = client.post("/chat", json={
        "task_description": "x",
        "message": "go",
        "required_tools": ["does_not_exist"],
    })
    assert r.status_code == 422
    assert "does_not_exist" in r.json()["detail"]
