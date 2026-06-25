import pytest
from fastapi.testclient import TestClient
from mcp_server.log_mock.api import app

client = TestClient(app)

def test_search_returns_hits():
    resp = client.post("/logs/_search", json={"size": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert "hits" in body
    assert len(body["hits"]["hits"]) == 5

def test_search_filters_by_level():
    resp = client.post("/logs/_search", json={"size": 100, "query": {"match": {"level": "ERROR"}}})
    assert resp.status_code == 200
    hits = resp.json()["hits"]["hits"]
    assert all(h["_source"]["level"] == "ERROR" for h in hits)

def test_search_total_reflects_filter():
    resp = client.post("/logs/_search", json={"size": 5, "query": {"match": {"level": "INFO"}}})
    body = resp.json()
    assert body["hits"]["total"]["value"] >= len(body["hits"]["hits"])

def test_search_default_size():
    resp = client.post("/logs/_search", json={})
    assert resp.status_code == 200
    assert len(resp.json()["hits"]["hits"]) == 10
