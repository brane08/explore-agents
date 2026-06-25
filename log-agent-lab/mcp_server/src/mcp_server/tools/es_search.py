from mcp_server.log_mock.corpus import CORPUS as _CORPUS


def es_search(query_level: str | None = None, size: int = 10) -> dict:
    """Search log records, optionally filtered by log level."""
    results = _CORPUS
    if query_level is not None:
        results = [r for r in results if r["level"] == query_level]
    hits = results[:size]
    return {"hits": [dict(r) for r in hits], "total": len(results)}
