"""
sandbox_runner.py — Step 4 of the B2a loop.

Executes a generated agent.py as a subprocess with a hard timeout.
The generated code never runs in the orchestrator's process.

run_in_sandbox() returns a SandboxResult regardless of outcome —
timeout, crash, and clean exit are all represented, never raised.
"""

from __future__ import annotations

import os
import subprocess
import sys
import sysconfig
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class SandboxResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    exit_code: int | None       # None when timed out
    stdout: str
    stderr: str
    timed_out: bool


def run_in_sandbox(
    agent_path: Path,
    mcp_server_url: str,
    timeout_seconds: int = 30,
    extra_env: dict[str, str] | None = None,
) -> SandboxResult:
    """
    Run agent_path as a subprocess using the current interpreter.

    The subprocess inherits a clean env with MCP_SERVER_URL, PYTHONPATH
    (agent dir + venv site-packages), and minimal system vars.
    extra_env can inject additional variables for testing.
    """
    # Include venv site-packages so the generated agent can import fastmcp/langgraph.
    # sysconfig paths are derived from sys.executable, so they're always correct
    # regardless of whether the venv is activated in the shell.
    _sp = {sysconfig.get_path("purelib"), sysconfig.get_path("platlib")} - {None}
    pythonpath = os.pathsep.join([str(agent_path.parent)] + sorted(_sp))

    env = {
        "MCP_SERVER_URL": mcp_server_url,
        "PYTHONPATH": pythonpath,
        "PATH": os.environ.get("PATH", ""),
        # propagate HOME so fastmcp cache dirs resolve
        "HOME": os.environ.get("HOME", ""),
    }
    if extra_env:
        env.update(extra_env)

    try:
        proc = subprocess.run(
            [sys.executable, str(agent_path)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env,
        )
        return SandboxResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            timed_out=False,
        )
    except subprocess.TimeoutExpired as exc:
        return SandboxResult(
            exit_code=None,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            timed_out=True,
        )
