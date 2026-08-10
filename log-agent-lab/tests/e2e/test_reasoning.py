"""
End-to-end B2b: generate an agent from a natural-language task, run it in a
sandbox, and grade its output against ground truth computed live from the real
tools. Marked `e2e` (boots a live MCP).

- oracle path proves the full harness end to end without a model (a perfect
  reasoner passes every held-out task);
- the wrong-plan test proves the grader actually discriminates;
- an opt-in test drives a real LLM over a held-out task when configured.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))  # tests/ — serverkit
from serverkit import boot_mcp

sys.path.insert(0, str(Path(__file__).parents[2] / "b2a_app" / "src"))
from eval_suite.reasoning_tasks import GAP_TASKS, HELDOUT_TASKS
from loop.reasoning import (
    oracle_completer,
    render_answer_agent,
    render_missing_agent,
    run_reasoning_loop,
    run_reasoning_suite,
    run_reasoning_task,
)
from loop.skills import load_skill_tools

pytestmark = pytest.mark.e2e

_SKILLS = Path(__file__).parents[3]  # platform catalog root


def routing_oracle(mcp_url: str):
    """Test double for a competent model: routes each generator prompt to the
    correct agent (or gap report) by matching the task's request text."""
    def complete(prompt: str) -> str:
        for t in HELDOUT_TASKS:
            if t.prompt in prompt:
                if not t.answerable:
                    hint = t.gap_hints[0] if t.gap_hints else "capability"
                    return render_missing_agent(
                        f"No available tool provides the required {hint} capability."
                    )
                return render_answer_agent(t.tool_name, t.arguments, mcp_url)
        raise AssertionError("prompt did not contain any known task request")
    return complete


def test_oracle_agents_pass_every_heldout_task(tmp_path):
    catalog = load_skill_tools(_SKILLS)
    with boot_mcp() as mcp_url:
        results = [
            run_reasoning_task(t, catalog, mcp_url, oracle_completer(t, mcp_url), tmp_path)
            for t in HELDOUT_TASKS
        ]
    failed = [(r.task_id, r.detail) for r in results if not r.passed]
    assert failed == [], failed
    # answerable tasks carry an answer; gap tasks carry a missing report
    by_id = {t.id: t for t in HELDOUT_TASKS}
    assert all(
        r.answer if by_id[r.task_id].answerable else r.missing
        for r in results
    )


def test_grader_rejects_a_wrong_plan(tmp_path):
    task = HELDOUT_TASKS[0]  # "list the error-level entries" → es_search ERROR
    catalog = load_skill_tools(_SKILLS)

    def wrong(_prompt: str) -> str:  # answers a different question
        return render_answer_agent("field_stats", {"field": "level"}, "")

    with boot_mcp() as mcp_url:
        result = run_reasoning_task(task, catalog, mcp_url, wrong, tmp_path)
    assert not result.passed
    assert "does not match" in result.detail


def test_feedback_loop_recovers_from_wrong_plan(tmp_path):
    """First attempt answers the wrong question; the failure feedback must
    drive a corrected second attempt — proves retry actually consumes feedback."""
    task = HELDOUT_TASKS[0]
    catalog = load_skill_tools(_SKILLS)

    def wrong_then_right(mcp_url):
        def complete(prompt: str) -> str:
            if "previous attempt" in prompt.lower():   # feedback present → correct plan
                return render_answer_agent(task.tool_name, task.arguments, mcp_url)
            return render_answer_agent("field_stats", {"field": "level"}, mcp_url)
        return complete

    with boot_mcp() as mcp_url:
        report = run_reasoning_loop(
            task, catalog, mcp_url, wrong_then_right(mcp_url), tmp_path, max_attempts=3
        )
    assert report.passed
    assert report.attempts == 2
    assert not report.results[0].passed and report.results[1].passed


def test_suite_reports_heldout_pass_rate(tmp_path):
    catalog = load_skill_tools(_SKILLS)
    with boot_mcp() as mcp_url:
        report = run_reasoning_suite(
            HELDOUT_TASKS, catalog, mcp_url, routing_oracle(mcp_url), tmp_path, max_attempts=1
        )
    assert report.total == len(HELDOUT_TASKS)
    assert report.passed == report.total
    assert report.pass_rate == 1.0
    # per-kind breakdown + the actionable missing-capability highlight
    assert report.gap_total == len(GAP_TASKS)
    assert report.gaps_detected == report.gap_total
    assert report.answerable_passed == report.answerable_total == report.total - report.gap_total
    assert len(report.missing_capabilities) == report.gap_total
    assert all(m.strip() for m in report.missing_capabilities)


def test_feedback_loop_recovers_on_gap_task(tmp_path):
    """First attempt fabricates a call on an unanswerable task; the generic
    failure feedback must drive it to reconsider and report the gap."""
    task = GAP_TASKS[0]  # delete_old_errors
    catalog = load_skill_tools(_SKILLS)

    def fabricate_then_flag(mcp_url):
        def complete(prompt: str) -> str:
            if "previous attempt" in prompt.lower():
                return render_missing_agent("No tool can delete or purge log records.")
            return render_answer_agent("es_search", {"query_level": "ERROR", "size": 10}, mcp_url)
        return complete

    with boot_mcp() as mcp_url:
        report = run_reasoning_loop(
            task, catalog, mcp_url, fabricate_then_flag(mcp_url), tmp_path, max_attempts=3
        )
    assert report.passed and report.attempts == 2
    assert report.results[-1].missing


def test_run_reasoning_endpoint(tmp_path, monkeypatch):
    """Drive /run-reasoning end to end with an injected competent completer."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "b2a_api_e2e", Path(__file__).parents[2] / "b2a_app" / "src" / "api.py"
    )
    api = importlib.util.module_from_spec(spec)
    sys.modules["b2a_api_e2e"] = api
    spec.loader.exec_module(api)

    from fastapi.testclient import TestClient

    with boot_mcp() as mcp_url:
        monkeypatch.setenv("MCP_SERVER_URL", mcp_url)
        monkeypatch.setattr(api, "_completer_factory", lambda: routing_oracle(mcp_url))
        with TestClient(api.app) as client:   # lifespan → skills discovery
            r = client.post("/run-reasoning", json={"max_attempts": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == len(HELDOUT_TASKS)
    assert body["pass_rate"] == 1.0
    assert all(t["passed"] for t in body["tasks"])
    assert body["gap_total"] == len(GAP_TASKS)
    assert body["gaps_detected"] == body["gap_total"]
    assert len(body["missing_capabilities"]) == body["gap_total"]


def test_skills_catalog_matches_live_mcp():
    """Drift guard: the hand-written skills catalog must describe the same
    tools (names + parameter names) the live MCP server actually serves."""
    import anyio
    from fastmcp import Client

    catalog = {t.name: t for t in load_skill_tools(_SKILLS)}

    async def fetch(url):
        async with Client(url.rstrip("/") + "/mcp") as c:
            return await c.list_tools()

    with boot_mcp() as mcp_url:
        live = anyio.run(fetch, mcp_url)

    live_by_name = {t.name: t for t in live}
    assert set(live_by_name) == set(catalog), "tool names drifted"
    for name, skill in catalog.items():
        live_schema = live_by_name[name].inputSchema
        live_props = set((live_schema.get("properties") or {}))
        skill_props = set((skill.input_schema.get("properties") or {}))
        assert skill_props == live_props, f"{name}: parameter names drifted"


@pytest.mark.skipif(
    not os.environ.get("B2A_LLM_PROVIDER"),
    reason="set B2A_LLM_PROVIDER (+ creds) to test real reasoning over held-out tasks",
)
def test_real_provider_heldout_pass_rate(tmp_path):
    """Opt-in capability measurement: run the whole held-out suite through the
    configured real provider and report N/total. Pass rate is printed, not
    asserted at 100% — model quality varies; the floor is >0 with feedback."""
    from loop.llm import select_completer

    completer = select_completer()
    catalog = load_skill_tools(_SKILLS)
    with boot_mcp() as mcp_url:
        report = run_reasoning_suite(
            HELDOUT_TASKS, catalog, mcp_url, completer, tmp_path,
            max_attempts=3, sandbox_timeout=90,
        )
    for r in report.reports:
        print(f"  {r.task_id}: {'PASS' if r.passed else 'FAIL'} in {r.attempts} attempt(s)")
    print(f"held-out pass rate: {report.passed}/{report.total} = {report.pass_rate:.0%}")
    print(f"  answerable: {report.answerable_passed}/{report.answerable_total}"
          f" | gaps detected: {report.gaps_detected}/{report.gap_total}")
    for m in report.missing_capabilities:
        print(f"  missing capability: {m}")
    assert report.passed > 0, "real provider solved zero held-out tasks even with feedback"
