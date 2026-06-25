from fastmcp import FastMCP
from mcp_server.tools.es_search import es_search
from mcp_server.tools.es_aggregate import es_aggregate
from mcp_server.tools.field_stats import field_stats

mcp = FastMCP(name="log-mcp-server")

mcp.tool()(es_search)
mcp.tool()(es_aggregate)
mcp.tool()(field_stats)

# ASGI app for uvicorn: uvicorn mcp_server.server:app --port 8000
app = mcp.http_app()
