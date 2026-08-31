"""CHECKLISTS layer 10 [M] (Phase 1 subset) — error fragments, SSE-as-trace,
reconnect replay, operator gating."""
from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from orch_fixtures import TASK
from orchestrator.errors import CODES, StructuredError
from test_layer3_routing import last_invocation

TEMPLATES = Path(__file__).parents[1] / "src" / "orchestrator" / "templates"


def test_every_error_code_has_a_fragment_renderer():
    env = Environment(loader=FileSystemLoader(TEMPLATES),
                      autoescape=select_autoescape(["html", "j2"]))
    for code in CODES:
        html = env.get_template(f"errors/{code}.html.j2").render(
            error=StructuredError(code, "some context"))
        assert code in html
        assert "some context" in html


def _sse_frames(client, url, headers=None):
    frames, current = [], {}
    with client.stream("GET", url, headers=headers or {}) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line == "":
                if current:
                    frames.append(current)
                current = {}
            elif ":" in line:
                k, v = line.split(":", 1)
                current[k.strip()] = v.strip()
    if current:
        frames.append(current)
    return [f for f in frames if "event" in f]


def test_sse_stream_matches_persisted_trace_1_to_1(client, store):
    client.get("/")
    client.post("/tasks", data={"task": TASK})
    iid = last_invocation(store, client)

    frames = _sse_frames(client, f"/invocations/{iid}/stream")
    stored = store.events_after(iid, 0)
    assert [f["event"] for f in frames] == [e.event_type for e in stored]
    assert [int(f["id"]) for f in frames] == [e.seq for e in stored]
    assert frames[-1]["event"] == "terminal"


def test_reconnect_replays_via_last_event_id(client, store):
    client.get("/")
    client.post("/tasks", data={"task": TASK})
    iid = last_invocation(store, client)
    total = len(store.events_after(iid, 0))

    frames = _sse_frames(client, f"/invocations/{iid}/stream",
                         headers={"Last-Event-ID": "2"})
    assert [int(f["id"]) for f in frames] == list(range(3, total + 1))


def test_invocation_detail_replays_closed_trace(client, store):
    client.get("/")
    client.post("/tasks", data={"task": TASK})
    iid = last_invocation(store, client)
    r = client.get(f"/invocations/{iid}")
    assert r.status_code == 200
    for etype in ("invocation", "tool_call", "terminal"):
        assert f'data-type="{etype}"' in r.text


def test_invocation_detail_and_stream_refuse_a_different_users_session(client, store):
    """invoke-ui#9: an invocation id is low-enumerability but not access
    control — a caller with no session, or a different user's session, must
    not be able to read another user's trace via either endpoint."""
    client.get("/")
    client.post("/tasks", data={"task": TASK})
    iid = last_invocation(store, client)

    from fastapi.testclient import TestClient
    other = TestClient(client.app)                       # no cookie at all
    r = other.get(f"/invocations/{iid}")
    assert r.status_code == 403
    r = other.get(f"/invocations/{iid}/stream")
    assert r.status_code == 403

    other.get("/", headers={"X-Forwarded-User": "someone-else@example.com"})
    r = other.get(f"/invocations/{iid}")
    assert r.status_code == 403


def test_operator_may_read_any_users_invocation(client, store):
    client.get("/")
    client.post("/tasks", data={"task": TASK})
    iid = last_invocation(store, client)

    from fastapi.testclient import TestClient
    op = TestClient(client.app)
    r = op.get(f"/invocations/{iid}", headers={"X-Forwarded-Roles": "operator"})
    assert r.status_code == 200


def test_meta_screens_gated_by_operator_role(client):
    client.get("/")
    assert client.get("/agents").status_code == 403
    r = client.get("/agents", headers={"X-Forwarded-Roles": "operator"})
    assert r.status_code == 200
    assert "Catalog browser" in r.text
