import json
from fastmcp import Client
from orchestrator.tool_spec import ToolSpec


class MCPClient:
    def __init__(self, base_url: str) -> None:
        # FastMCP SSE endpoint is at /mcp
        self._mcp_url = base_url.rstrip("/") + "/mcp"

    async def list_tools(self) -> list[ToolSpec]:
        async with Client(self._mcp_url) as client:
            tools = await client.list_tools()
        return [
            ToolSpec(
                name=t.name,
                description=t.description or "",
                input_schema=t.inputSchema.model_dump() if hasattr(t.inputSchema, "model_dump") else dict(t.inputSchema),
            )
            for t in tools
        ]

    async def call_tool(self, name: str, arguments: dict) -> dict:
        async with Client(self._mcp_url) as client:
            result = await client.call_tool(name, arguments)
        # result is a CallToolResult; content is the list of blocks
        for block in result.content:
            if hasattr(block, "text"):
                try:
                    return json.loads(block.text)
                except (json.JSONDecodeError, TypeError):
                    return {"text": block.text}
        return {"result": str(result)}
