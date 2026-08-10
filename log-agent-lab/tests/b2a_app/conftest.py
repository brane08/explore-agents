import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "b2a_app" / "src"))
sys.path.insert(0, str(Path(__file__).parents[1]))  # tests/ — for serverkit

import pytest

from serverkit import boot_mcp


@pytest.fixture(scope="module")
def live_mcp_url():
    """Boot the real reference MCP server for the integration-flavoured eval-case tests."""
    with boot_mcp() as url:
        yield url
