"""Operator-triggered B2b generation (ROADMAP §3 exit criterion).

The cascade's last step records a residual and stops; this is the surface that
turns that residual into a Role A dispatch. What it owes: operator gating, the
cascade re-checked so generation cannot manufacture a duplicate capability,
criteria authored rather than supplied, and a candidate that lands in the
certify queue and nowhere else.
"""
from __future__ import annotations

import yaml

from orch_fixtures import GIBBERISH, TASK, git  # noqa: F401 — fixtures via conftest

OP = {"X-Forwarded-Roles": "operator"}

CONFORMANT = {"harnesses": [{"harness": "stub-harness/0", "conformant": True,
                             "recorded_at": "2026-07-16T00:00:00+00:00"}]}


def _list_harness(catalog_repo, settings=None) -> None:
    (catalog_repo / "conformance.yaml").write_text(
        yaml.safe_dump(CONFORMANT), encoding="utf-8")


def test_offer_is_operator_only(client, catalog_repo):
    client.get("/")
    plain = client.post("/tasks", data={"task": GIBBERISH})
    assert "PATTERN_UNRECOGNIZED" in plain.text
    assert "/generate" not in plain.text

    op = client.post("/tasks", data={"task": GIBBERISH}, headers=OP)
    assert "/generate" in op.text


def test_generate_requires_operator(client, catalog_repo):
    client.get("/")
    assert client.post("/generate", data={"task": GIBBERISH}).status_code == 403


def test_generate_dispatches_and_lands_in_the_certify_queue(client, catalog_repo):
    _list_harness(catalog_repo)
    client.get("/")
    r = client.post("/generate", data={"task": GIBBERISH}, headers=OP,
                    follow_redirects=False)
    assert r.status_code == 303, r.text
    cid = r.headers["location"].rsplit("/", 1)[1]

    cdir = catalog_repo / "agents" / "_proposed" / cid
    assert cdir.is_dir()
    # nothing promoted: the catalog gains no agent, only a queue entry
    assert not [p for p in (catalog_repo / "agents").iterdir() if p.name != "_proposed"]
    assert client.get("/certify", headers=OP).text.count(cid) >= 1


def test_generated_criteria_are_authored_not_supplied(client, catalog_repo):
    _list_harness(catalog_repo)
    client.get("/")
    r = client.post("/generate", data={"task": GIBBERISH}, headers=OP,
                    follow_redirects=False)
    cid = r.headers["location"].rsplit("/", 1)[1]
    sidecar = yaml.safe_load(
        (catalog_repo / "agents" / "_proposed" / f"{cid}.inputs.yaml")
        .read_text(encoding="utf-8"))

    assert sidecar["criteria_authored_by"], "the author must be recorded"
    assert sidecar["behavioral_criteria"].startswith("BC-1: ")
    assert sidecar["task_spec"] == GIBBERISH


def test_generation_is_refused_for_a_task_the_cascade_covers(client, catalog_repo):
    """The cascade is the control against generating a second copy of an
    existing capability; the form must not be a way around it."""
    _list_harness(catalog_repo)
    client.get("/")
    r = client.post("/generate", data={"task": TASK}, headers=OP)
    assert r.status_code == 409
    assert "PATTERN_UNRECOGNIZED" in r.json()["detail"]
    proposed = catalog_repo / "agents" / "_proposed"
    assert not list(proposed.iterdir())


def test_generation_is_refused_when_the_harness_is_not_conformant(client, catalog_repo):
    """CATALOG §10: an unmeasured harness never generates a candidate."""
    client.get("/")
    r = client.post("/generate", data={"task": GIBBERISH}, headers=OP)
    assert r.status_code == 409
    assert "conformance" in r.json()["detail"]
    assert not list((catalog_repo / "agents" / "_proposed").iterdir())


def test_generation_is_refused_when_criteria_cannot_be_authored(
        client, catalog_repo, monkeypatch):
    from orchestrator import webapp as webapp_mod
    from orchestrator.criteria import CriteriaError

    def broken(*a, **kw):
        raise CriteriaError("criteria author unreachable")

    monkeypatch.setattr(webapp_mod, "dispatch_b2b", broken)
    _list_harness(catalog_repo)
    client.get("/")
    r = client.post("/generate", data={"task": GIBBERISH}, headers=OP)
    assert r.status_code == 409
    assert "criteria could not be authored" in r.json()["detail"]
    assert not list((catalog_repo / "agents" / "_proposed").iterdir())
