"""
Tests for sandbox_runner. Uses real subprocesses against tmp scripts —
no mocking of subprocess itself, since the point is to verify the
containment boundary works.
"""

import json
from pathlib import Path
import pytest
from loop.sandbox_runner import SandboxResult, run_in_sandbox


def _write_script(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "agent.py"
    p.write_text(content)
    return p


def test_clean_exit_captured(tmp_path):
    script = _write_script(tmp_path, 'import json\nprint(json.dumps({"ok": True}))\n')
    result = run_in_sandbox(script, mcp_server_url="http://localhost:8000")
    assert result.exit_code == 0
    assert not result.timed_out
    assert json.loads(result.stdout) == {"ok": True}


def test_crash_captured(tmp_path):
    script = _write_script(tmp_path, "raise RuntimeError('boom')\n")
    result = run_in_sandbox(script, mcp_server_url="http://localhost:8000")
    assert result.exit_code != 0
    assert not result.timed_out
    assert "boom" in result.stderr


def test_timeout_detected(tmp_path):
    script = _write_script(tmp_path, "import time\ntime.sleep(60)\n")
    result = run_in_sandbox(script, mcp_server_url="http://localhost:8000", timeout_seconds=1)
    assert result.timed_out
    assert result.exit_code is None


def test_mcp_server_url_in_env(tmp_path):
    script = _write_script(
        tmp_path,
        'import json, os\nprint(json.dumps({"url": os.environ["MCP_SERVER_URL"]}))\n',
    )
    result = run_in_sandbox(script, mcp_server_url="http://test-server:9999")
    assert result.exit_code == 0
    assert json.loads(result.stdout)["url"] == "http://test-server:9999"


def test_result_is_frozen(tmp_path):
    script = _write_script(tmp_path, "print('hi')\n")
    result = run_in_sandbox(script, mcp_server_url="http://localhost:8000")
    with pytest.raises(Exception):
        result.exit_code = 99


def test_stderr_captured_on_clean_exit(tmp_path):
    script = _write_script(tmp_path, "import sys\nsys.stderr.write('warn\\n')\nprint('ok')\n")
    result = run_in_sandbox(script, mcp_server_url="http://localhost:8000")
    assert result.exit_code == 0
    assert "warn" in result.stderr
    assert "ok" in result.stdout


def test_extra_env_injected(tmp_path):
    script = _write_script(
        tmp_path,
        'import json, os\nprint(json.dumps({"x": os.environ.get("MY_VAR")}))\n',
    )
    result = run_in_sandbox(
        script,
        mcp_server_url="http://localhost:8000",
        extra_env={"MY_VAR": "hello"},
    )
    assert json.loads(result.stdout)["x"] == "hello"


def test_orchestrator_env_not_leaked(tmp_path):
    """Generated code should not inherit the orchestrator's full environment."""
    import os
    os.environ["SECRET_SHOULD_NOT_LEAK"] = "topsecret"
    script = _write_script(
        tmp_path,
        'import json, os\nprint(json.dumps({"s": os.environ.get("SECRET_SHOULD_NOT_LEAK")}))\n',
    )
    result = run_in_sandbox(script, mcp_server_url="http://localhost:8000")
    assert json.loads(result.stdout)["s"] is None
