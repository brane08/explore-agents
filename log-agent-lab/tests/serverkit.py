"""
Shared helpers for booting the log-agent-lab services as subprocesses in tests.
Used by the e2e suite and by the integration-flavoured eval-case tests.
"""
from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).parents[1]  # log-agent-lab/


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def spawn(app: str, src: Path, port: int, env_extra: dict[str, str] | None = None) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src)
    env.update(env_extra or {})
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", app, "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(src),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def wait_http(url: str, proc: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"process for {url} exited early:\n{proc.communicate()[0]}")
        try:
            if httpx.get(url, timeout=1.0).status_code < 500:
                return
        except httpx.HTTPError:
            time.sleep(0.4)
    raise TimeoutError(f"{url} not ready within {timeout}s")


def wait_mcp(base_url: str, proc: subprocess.Popen, timeout: float = 30.0) -> None:
    """Poll the MCP endpoint by listing tools with a fastmcp client."""
    import anyio
    from fastmcp import Client

    async def _list() -> int:
        async with Client(base_url.rstrip("/") + "/mcp") as client:
            return len(await client.list_tools())

    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"mcp_server exited early:\n{proc.communicate()[0]}")
        try:
            if anyio.run(_list) > 0:
                return
        except Exception:
            time.sleep(0.5)
    raise TimeoutError(f"mcp_server not serving tools within {timeout}s")


def terminate(procs: list[subprocess.Popen]) -> None:
    for p in reversed(procs):
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()


@contextlib.contextmanager
def boot_mcp():
    """Boot mcp_server on a free port; yield its base URL; tear down."""
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    proc = spawn("server:app", ROOT / "mcp_server" / "src", port)
    try:
        wait_mcp(url, proc)
        yield url
    finally:
        terminate([proc])
