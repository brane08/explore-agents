import pytest
from mcp_server.tools.es_search import es_search
from mcp_server.tools.es_aggregate import es_aggregate
from mcp_server.tools.field_stats import field_stats

def test_es_search_returns_hits():
    result = es_search(query_level=None, size=3)
    assert "hits" in result
    assert len(result["hits"]) == 3

def test_es_search_filters_level():
    result = es_search(query_level="ERROR", size=50)
    assert all(h["level"] == "ERROR" for h in result["hits"])

def test_es_search_no_match_returns_empty():
    result = es_search(query_level="BOGUS", size=10)
    assert result["hits"] == []
    assert result["total"] == 0

def test_es_aggregate_by_level():
    result = es_aggregate(group_by="level", metric="count")
    assert "buckets" in result
    assert any(b["key"] == "ERROR" for b in result["buckets"])

def test_es_aggregate_invalid_field_raises():
    with pytest.raises(ValueError, match="Unknown group_by field"):
        es_aggregate(group_by="nonexistent_field", metric="count")

def test_field_stats_level():
    result = field_stats(field="level")
    assert "cardinality" in result
    assert "top_values" in result
    assert result["cardinality"] >= 1

def test_field_stats_duration_ms():
    result = field_stats(field="duration_ms")
    assert "min" in result
    assert "max" in result
    assert "avg" in result

def test_field_stats_unknown_field_raises():
    with pytest.raises(ValueError, match="Unknown field"):
        field_stats(field="does_not_exist")
