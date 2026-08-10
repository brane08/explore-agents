"""
E2E fixtures — boot the three services as real subprocesses and drive them
over HTTP. Nothing is imported in-process, so the `api` module-name collision
between agent_app and b2a_app never arises.

Boot order matters: the reference MCP server must be serving MCP before agent_app / b2a_app
start, because b2a_app fetches the tool pool once in its lifespan.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))  # tests/ — for serverkit
from serverkit import ROOT, TOOL_PLANE, free_port, spawn, terminate, wait_http, wait_mcp


@pytest.fixture(scope="session")
def services():
    """Boot all three services; yield their base URLs; tear down."""
    mcp_port, agent_port, b2a_port = free_port(), free_port(), free_port()
    mcp_url = f"http://127.0.0.1:{mcp_port}"
    procs: list[subprocess.Popen] = []
    try:
        mcp = spawn("server:app", TOOL_PLANE, mcp_port)
        procs.append(mcp)
        wait_mcp(mcp_url, mcp)

        agent = spawn("api:app", ROOT / "agent_app" / "src", agent_port, {"MCP_SERVER_URL": mcp_url})
        procs.append(agent)
        wait_http(f"http://127.0.0.1:{agent_port}/health", agent)

        b2a = spawn("api:app", ROOT / "b2a_app" / "src", b2a_port, {"MCP_SERVER_URL": mcp_url})
        procs.append(b2a)
        wait_http(f"http://127.0.0.1:{b2a_port}/health", b2a)

        yield {
            "mcp": mcp_url,
            "agent": f"http://127.0.0.1:{agent_port}",
            "b2a": f"http://127.0.0.1:{b2a_port}",
        }
    finally:
        terminate(procs)
