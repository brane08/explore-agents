"""Reference MCP server — a stand-in for a tool plane the platform does not own.

`mcp-tool` is an *external* kind (docs/CATALOG.md): entries carry a
`registry_url` and a mirrored `schema.snapshot.json`, and the schema is the
contract boundary. In a real deployment the server behind that URL belongs to
someone else. This one exists so the seeded catalog has something to invoke.

That is why it lives outside the uv workspace and why nothing under `tooling/`
imports it: the only supported coupling is HTTP via `MCP_SERVER_URL`. Reaching
into these modules from platform code would collapse the boundary the snapshot
mechanism exists to enforce.

The three tools here back `skills/es_search`, `skills/es_aggregate` and
`mcp/field_stats`. `tooling/lockbuild/tests/test_snapshot_contract.py` asserts
the mirrored snapshots still match what this server serves.
"""
from fastmcp import FastMCP
from tools.es_search import es_search
from tools.es_aggregate import es_aggregate
from tools.field_stats import field_stats

mcp = FastMCP(name="log-mcp-server")

mcp.tool()(es_search)
mcp.tool()(es_aggregate)
mcp.tool()(field_stats)

app = mcp.http_app()
