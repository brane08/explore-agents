from collections import Counter
from mcp_server.log_mock.corpus import CORPUS as _CORPUS
_NUMERIC_FIELDS = {"duration_ms", "status_code"}
_CATEGORICAL_FIELDS = {"level", "service", "message"}
_ALL_FIELDS = _NUMERIC_FIELDS | _CATEGORICAL_FIELDS


def field_stats(field: str) -> dict:
    """Return summary statistics for a single field across all log records."""
    if field not in _ALL_FIELDS:
        raise ValueError(f"Unknown field: {field!r}. Allowed: {sorted(_ALL_FIELDS)}")
    values = [r[field] for r in _CORPUS]
    if field in _NUMERIC_FIELDS:
        nums = [float(v) for v in values]
        return {
            "field": field,
            "min": min(nums),
            "max": max(nums),
            "avg": sum(nums) / len(nums),
            "count": len(nums),
        }
    counter = Counter(values)
    return {
        "field": field,
        "cardinality": len(counter),
        "top_values": [{"value": k, "count": v} for k, v in counter.most_common(10)],
    }
