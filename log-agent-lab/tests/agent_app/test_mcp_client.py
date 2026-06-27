import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from mcp_client import MCPClient


def _make_tool(name, description, schema=None):
    t = MagicMock()
    t.name = name
    t.description = description
    t.inputSchema = MagicMock()
    t.inputSchema.model_dump.return_value = schema or {"type": "object"}
    return t


def _make_text_block(text):
    block = MagicMock()
    block.text = text
    return block


@pytest.fixture
def client():
    return MCPClient(base_url="http://test-mcp")


@pytest.mark.anyio
async def test_list_tools_returns_tool_specs(client):
    mock_tools = [
        _make_tool("es_search", "Search logs"),
        _make_tool("field_stats", "Field stats"),
    ]
    mock_client = AsyncMock()
    mock_client.list_tools.return_value = mock_tools
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("mcp_client.Client", return_value=mock_client):
        tools = await client.list_tools()

    assert len(tools) == 2
    assert tools[0].name == "es_search"
    assert tools[1].name == "field_stats"


@pytest.mark.anyio
async def test_list_tools_input_schema_preserved(client):
    mock_tools = [_make_tool("es_search", "Search logs", {"type": "object", "properties": {}})]
    mock_client = AsyncMock()
    mock_client.list_tools.return_value = mock_tools
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("mcp_client.Client", return_value=mock_client):
        tools = await client.list_tools()

    assert tools[0].input_schema == {"type": "object", "properties": {}}


@pytest.mark.anyio
async def test_call_tool_returns_parsed_result(client):
    payload = json.dumps({"hits": [], "total": 0})
    mock_client = AsyncMock()
    mock_client.call_tool.return_value = [_make_text_block(payload)]
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("mcp_client.Client", return_value=mock_client):
        result = await client.call_tool("es_search", {"query_level": "ERROR", "size": 5})

    assert result == {"hits": [], "total": 0}


@pytest.mark.anyio
async def test_call_tool_server_error_raises(client):
    mock_client = AsyncMock()
    mock_client.call_tool.side_effect = Exception("MCP server error")
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("mcp_client.Client", return_value=mock_client):
        with pytest.raises(Exception, match="MCP server error"):
            await client.call_tool("es_search", {})
