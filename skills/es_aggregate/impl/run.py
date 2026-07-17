"""Thin runnable client for the es_aggregate capability (data, not a package).

Usage: MCP_SERVER_URL=http://localhost:8000 python run.py GROUP_BY
"""
import asyncio
import json
import os
import sys

from fastmcp import Client

MCP_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:8000").rstrip("/") + "/mcp"


async def main() -> None:
    result = {"error": "no result"}
    try:
        async with Client(MCP_URL) as client:
            raw = await client.call_tool("es_aggregate", {"group_by": sys.argv[1]})
        for block in raw.content:
            if hasattr(block, "text"):
                result = json.loads(block.text)
                break
    except Exception as exc:
        result = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(main())
