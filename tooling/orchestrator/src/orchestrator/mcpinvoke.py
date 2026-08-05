"""Real tool-plane invoker over MCP (optional extra: `orchestrator[mcp]`)."""
from __future__ import annotations

import json
import os

from orchestrator.executor import ToolInvoker


def mcp_invoker(mcp_server_url: str | None = None) -> ToolInvoker:
    import anyio
    from fastmcp import Client

    url = (mcp_server_url or os.environ.get("MCP_SERVER_URL", "http://localhost:8000")
           ).rstrip("/") + "/mcp"

    async def _call(tool_id: str, args: dict) -> dict:
        async with Client(url) as client:
            raw = await client.call_tool(tool_id, args)
        for block in raw.content:
            if hasattr(block, "text"):
                try:
                    return json.loads(block.text)
                except (json.JSONDecodeError, TypeError):
                    return {"text": block.text}
        return {"error": "empty tool result"}

    def invoke(tool_id: str, args: dict) -> dict:
        return anyio.run(_call, tool_id, args)

    return invoke
