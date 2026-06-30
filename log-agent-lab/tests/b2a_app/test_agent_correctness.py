"""
Integration test: generated agent output vs run_cases() ground truth.

Spins up a real mcp_server subprocess, runs the stub-generated agent in the
sandbox against it, then compares the agent's per-case output to the values
produced by run_cases() directly.  Catches agents that pass structural checks
by coincidence (wrong keys, wrong data, hardcoded responses).

Marked with pytest.mark.integration — excluded from fast unit runs.
Requires mcp_server and its dependencies on PYTHONPATH (satisfied by the uv
workspace when tests are run from log-agent-lab/).
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import sysconfig

import pytest

from eval_suite.log_analysis_cases import CASES, run_cases
from loop.generator import generate_candidate
from loop.sandbox_runner import run_in_sandbox
from loop.spec import EvalCaseSpec, TaskSpec, ToolSchema

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MCP_SRC = Path(__file__).parents[2] / "mcp_server" / "src"

def _venv_pythonpath(extra: str | None = None) -> str:
    parts = [p for p in (sysconfig.get_path("purelib"), sysconfig.get_path("platlib")) if p]
    if extra:
        parts.insert(0, extra)
    return __import__("os").pathsep.join(dict.fromkeys(parts))  # dedup, preserve order


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_http(url: str, timeout: float = 10.0) -> None:
    """Wait until the server accepts TCP connections (any HTTP response counts)."""
    import urllib.error
    import urllib.request
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except urllib.error.HTTPError:
            return  # server is up; 4xx/5xx still means it's listening
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"Server at {url} did not become ready in {timeout}s")


# ---------------------------------------------------------------------------
# Session-scoped MCP server fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def mcp_server_url():
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "server:app",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--log-level", "warning",
        ],
        cwd=_MCP_SRC,
        env={
            "PYTHONPATH": _venv_pythonpath(extra=str(_MCP_SRC)),
            "PATH": __import__("os").environ.get("PATH", ""),
            "HOME": __import__("os").environ.get("HOME", ""),
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_http(f"{url}/health")
    except RuntimeError:
        proc.terminate()
        proc.wait()
        raise
    yield url
    proc.terminate()
    proc.wait()


# ---------------------------------------------------------------------------
# Ground-truth fixture (run_cases directly against live server)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def ground_truth(mcp_server_url):
    results = asyncio.run(run_cases(mcp_server_url))
    return {r.case_id: r.result for r in results if r.result is not None}


# ---------------------------------------------------------------------------
# Generated agent fixture (stub generator, all eval cases)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def agent_sandbox_result(mcp_server_url, tmp_path_factory, ground_truth):
    tools = [
        ToolSchema(name="es_search", description="Search logs",
                   input_schema={"type": "object", "properties": {}}),
        ToolSchema(name="es_aggregate", description="Aggregate logs",
                   input_schema={"type": "object", "properties": {}}),
        ToolSchema(name="field_stats", description="Field statistics",
                   input_schema={"type": "object", "properties": {}}),
    ]
    eval_cases = [
        EvalCaseSpec(id=c.id, tool_name=c.tool_name, arguments=c.arguments)
        for c in CASES
    ]
    spec = TaskSpec(
        task_description="Analyse logs: search errors, aggregate by service, compute field stats.",
        mcp_server_url=mcp_server_url,
        available_tools=tools,
        eval_case_ids=[c.id for c in CASES],
        eval_cases=eval_cases,
    )
    base = tmp_path_factory.mktemp("generated")
    gen = generate_candidate(spec, iteration=0, generated_base_dir=base)
    return run_in_sandbox(gen.path, mcp_server_url=mcp_server_url, timeout_seconds=30)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_agent_exits_cleanly(agent_sandbox_result):
    assert not agent_sandbox_result.timed_out, "agent timed out"
    assert agent_sandbox_result.exit_code == 0, (
        f"agent crashed (exit {agent_sandbox_result.exit_code}):\n"
        f"{agent_sandbox_result.stderr[:400]}"
    )


@pytest.mark.integration
def test_agent_stdout_is_valid_json(agent_sandbox_result):
    try:
        json.loads(agent_sandbox_result.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"Agent stdout is not valid JSON: {exc}\n"
                    f"stdout: {agent_sandbox_result.stdout[:200]}")


@pytest.mark.integration
@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_agent_output_matches_ground_truth(agent_sandbox_result, ground_truth, case):
    """Agent's result for each case must equal the ground-truth value from run_cases()."""
    agent_outputs = json.loads(agent_sandbox_result.stdout)

    assert case.id in agent_outputs, (
        f"Case {case.id!r} missing from agent stdout. Keys present: "
        f"{list(agent_outputs)}"
    )
    assert case.id in ground_truth, (
        f"Case {case.id!r} missing from ground truth — run_cases() may have failed."
    )

    assert agent_outputs[case.id] == ground_truth[case.id], (
        f"Agent output for {case.id!r} differs from ground truth.\n"
        f"  agent:  {json.dumps(agent_outputs[case.id])}\n"
        f"  truth:  {json.dumps(ground_truth[case.id])}"
    )
