"""
End-to-end tests across mcp_server + agent_app + b2a_app, driven over HTTP.

Marked `e2e` (opt-in): `uv run pytest -q -m e2e`.

What each test proves:
  - b2a /health tools_loaded == 3  → b2a_app fetched the live tool pool at startup
  - agent /chat                    → agent_app assembled an agent and called live MCP tools
  - b2a /run-loop (stub backend)   → generate→sandbox→evaluate converges; the sandboxed
                                      agent subprocess connects to live MCP and passes every case
"""
from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.e2e

ALL_CASES = [
    "search_error_level",
    "aggregate_by_service",
    "field_stats_numeric",
    "field_stats_categorical",
]


def test_agent_and_b2a_health(services):
    r = httpx.get(f"{services['agent']}/health", timeout=5)
    assert r.status_code == 200 and r.json()["status"] == "ok"

    r = httpx.get(f"{services['b2a']}/health", timeout=5)
    body = r.json()
    assert r.status_code == 200
    # b2a_app pulled the tool pool from the live mcp_server in its lifespan
    assert body["tools_loaded"] == 3


def test_agent_chat_calls_live_mcp(services):
    # agent_app's /chat demo-calls each tool with no arguments, so this asserts
    # against es_search (whose params all default). es_aggregate would require
    # group_by — its empty-arg failure is agent_app's demo design, not a defect.
    r = httpx.post(
        f"{services['agent']}/chat",
        json={
            "task_description": "search error logs",
            "message": "go",
            "required_tools": ["es_search"],
        },
        timeout=30,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tools_used"] == ["es_search"]
    # the live tool call succeeded and real data was spliced into the response
    assert "ERROR —" not in body["response"]
    assert "hits" in body["response"]


def test_b2a_run_loop_converges(services):
    r = httpx.post(
        f"{services['b2a']}/run-loop",
        json={
            "eval_case_ids": ALL_CASES,
            "max_iterations": 1,
            "sandbox_timeout": 60,
        },
        timeout=180,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stopped_reason"] == "passed", body
    assert body["final_score"] == 1.0
    assert body["final_agent_path"]
    passed = {c["case_id"] for c in body["case_results"] if c["passed"]}
    assert passed == set(ALL_CASES)
