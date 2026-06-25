from collections import Counter
from mcp_server.log_mock.corpus import CORPUS as _CORPUS
_ALLOWED_FIELDS = {"level", "service", "status_code"}


def es_aggregate(group_by: str, metric: str = "count") -> dict:
    """Aggregate log records by a categorical field. metric must be 'count'."""
    if group_by not in _ALLOWED_FIELDS:
        raise ValueError(f"Unknown group_by field: {group_by!r}. Allowed: {sorted(_ALLOWED_FIELDS)}")
    if metric != "count":
        raise ValueError(f"Unsupported metric: {metric!r}. Only 'count' is supported.")
    counter = Counter(str(r[group_by]) for r in _CORPUS)
    buckets = [{"key": k, "count": v} for k, v in counter.most_common()]
    return {"group_by": group_by, "metric": metric, "buckets": buckets}
