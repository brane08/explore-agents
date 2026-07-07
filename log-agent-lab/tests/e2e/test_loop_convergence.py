"""
End-to-end proof of the B2a self-correction loop: generate -> fail eval ->
feed feedback back -> regenerate -> pass. Marked `e2e` (boots a live MCP).

The stub always passes on iteration 0, so on its own the loop never exercises
the feedback path. Here we inject a generator that deliberately produces an
incomplete agent on the first attempt (one eval case missing) and the complete
agent once feedback is present — so the loop must iterate to converge.

An optional, credential-gated test drives the same loop through a real LLM
provider when one is configured (skipped otherwise — no cost by default).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))  # tests/ — for serverkit
from serverkit import boot_mcp

sys.path.insert(0, str(Path(__file__).parents[2] / "b2a_app" / "src"))
from eval_suite.log_analysis_cases import CASES
from loop.generator import _render_agent, stub_generate
from loop.orchestrator import run_loop
from loop.spec import EvalCaseSpec, TaskSpec

pytestmark = pytest.mark.e2e

_CASES = [EvalCaseSpec(id=c.id, tool_name=c.tool_name, arguments=c.arguments) for c in CASES]


def _spec(mcp_url: str) -> TaskSpec:
    return TaskSpec(
        task_description="Analyse logs",
        mcp_server_url=mcp_url,
        available_tools=[],
        eval_case_ids=[c.id for c in CASES],
        eval_cases=_CASES,
        max_iterations=3,
    )


def _flaky_generator(spec: TaskSpec, feedback: str):
    """First attempt (no feedback): drop the last eval case so it fails.
    After feedback: emit the complete agent, which passes."""
    if not feedback.strip():
        partial = spec.model_copy(update={"eval_cases": spec.eval_cases[:-1]})
        return "partial", _render_agent(partial, feedback)
    return stub_generate(spec, feedback)


def test_loop_converges_after_feedback(tmp_path):
    with boot_mcp() as mcp_url:
        report = run_loop(
            _spec(mcp_url),
            generator_fn=_flaky_generator,
            sandbox_timeout=60,
            generated_base_dir=tmp_path,
        )

    assert report.stopped_reason == "passed", report
    assert report.iterations_run == 2                 # failed once, then passed
    assert report.final_score == 1.0
    assert 0.0 < report.trend[0] < 1.0                # first attempt partially failed
    # the second iteration actually consumed the feedback from the first
    assert "Iteration 0 failed" in report.records[1].feedback_used


def test_tool_failure_does_not_crash_agent(tmp_path):
    """A runtime tool error must surface as an eval failure (retryable), not a
    hard sandbox crash — proves the generated nodes are resilient."""
    bad_case = EvalCaseSpec(
        id="aggregate_by_service", tool_name="es_aggregate",
        arguments={"group_by": "BOGUS_FIELD"},  # server raises ValueError
    )
    spec = TaskSpec(
        task_description="Analyse logs",
        mcp_server_url="",  # filled below
        available_tools=[],
        eval_case_ids=[bad_case.id],
        eval_cases=[bad_case],
        max_iterations=2,
    )
    with boot_mcp() as mcp_url:
        spec = spec.model_copy(update={"mcp_server_url": mcp_url})
        report = run_loop(spec, sandbox_timeout=60, generated_base_dir=tmp_path)

    assert report.stopped_reason == "ceiling"          # retried, never "crash"
    assert all(r.sandbox and r.sandbox.exit_code == 0 for r in report.records)
    assert report.final_score < 1.0


@pytest.mark.skipif(
    not os.environ.get("B2A_LLM_PROVIDER"),
    reason="set B2A_LLM_PROVIDER (+ creds) to smoke-test a real LLM backend",
)
def test_real_provider_generates_runnable_agent(tmp_path):
    """Opt-in: drive the loop through the configured real provider once."""
    with boot_mcp() as mcp_url:
        report = run_loop(_spec(mcp_url), sandbox_timeout=90, generated_base_dir=tmp_path)
    # We don't require convergence (model quality varies) — only that the loop
    # ran a real generation and produced a report without crashing the harness.
    assert report.iterations_run >= 1
    assert report.records[0].generated_path is not None
