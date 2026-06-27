from fastmcp import FastMCP
from tools.es_search import es_search
from tools.es_aggregate import es_aggregate
from tools.field_stats import field_stats

mcp = FastMCP(name="log-mcp-server")

mcp.tool()(es_search)
mcp.tool()(es_aggregate)
mcp.tool()(field_stats)

app = mcp.http_app()
