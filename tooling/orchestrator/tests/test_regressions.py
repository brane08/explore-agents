"""Regression tests for review findings (layers 3, 4, 10).

Each test pins a refusal or failure path that the first Phase 1 cut got
wrong: recall of a config-only assembly, direct-invoke bypasses, unresolved
bindings, and background-task exception safety.
"""
from __future__ import annotations

import yaml

from orch_fixtures import TASK, demote_all, rebuild_and_commit
from orchestrator import assembly as assembly_mod
from orchestrator.trustcheck import load_records, transitive_tier
from test_layer3_routing import last_invocation, routing_steps
from test_layer4_b1 import b1_dirs


def _recall(root, entry_id: str, tier: str = "untrusted") -> None:
    """Append a recall record — the ledger is append-only (CATALOG §7)."""
    trust = root / "trust.yaml"
    data = yaml.safe_load(trust.read_text(encoding="utf-8"))
    data["records"].append({
        "id": entry_id, "version": "1.0.0", "model_profile": "stub-class-ref",
        "tier": tier, "granted_by": "test-recall", "evidence": "incident",
    })
    trust.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")


def test_recall_of_b1_assembly_takes_effect_at_routing_time(client, store, catalog_repo):
    """A recalled B1 registration must not keep routing just because its
    bindings are still validated — the derived tier never overrides a record."""
    client.get("/")
    client.post("/tasks", data={"task": TASK})
    agent_id = b1_dirs(catalog_repo)[0].name

    _recall(catalog_repo, agent_id)          # bindings stay validated
    client.post("/session/new")
    r = client.post("/tasks", data={"task": TASK})
    assert "STALE_ENTRY" in r.text
    assert any(entry == agent_id and reason == "tier:untrusted"
               for entry, reason in store.stale_queue())


def test_direct_invoke_refuses_stale_entry(client, store, catalog_repo):
    """The catalog-browser form is not a bypass around the cascade's refusals."""
    client.get("/")
    client.post("/tasks", data={"task": TASK})
    agent_id = b1_dirs(catalog_repo)[0].name

    impl = catalog_repo / "skills" / "es_search" / "impl" / "run.py"
    impl.write_text(impl.read_text() + "\n# drifted\n", encoding="utf-8")
    rebuild_and_commit(catalog_repo, "drift es_search")

    client.post("/session/new")
    r = client.post(f"/agents/{agent_id}/invoke", data={"task": TASK},
                    headers={"X-Forwarded-Roles": "operator"})
    assert r.status_code == 409
    assert "STALE_ENTRY" in r.text
    assert any("drift" in reason for _, reason in store.stale_queue())


def test_direct_invoke_refuses_when_a_binding_is_missing_at_the_pin(
        client, store, catalog_repo, settings):
    """An agent must never silently run with fewer tools than it declares."""
    import shutil

    client.get("/")
    client.post("/tasks", data={"task": TASK})
    agent_id = b1_dirs(catalog_repo)[0].name

    # drop a bound skill from the tree; the agent entry survives in an older
    # lock, so a session pinned there resolves the agent but not its binding
    entry = yaml.safe_load(
        (catalog_repo / "agents" / agent_id / "entry.yaml").read_text(encoding="utf-8"))
    bound = entry["bindings"][0]["id"]
    kind_dir = "skills" if (catalog_repo / "skills" / bound).is_dir() else "mcp"
    stale_lock = (catalog_repo / "catalog.lock.yaml").read_text(encoding="utf-8")
    shutil.rmtree(catalog_repo / kind_dir / bound)
    # the lock at the pinned ref still lists both; strip the binding from it
    lock_doc = yaml.safe_load(stale_lock)
    lock_doc["entries"] = [e for e in lock_doc["entries"] if e["id"] != bound]
    (catalog_repo / "catalog.lock.yaml").write_text(
        yaml.safe_dump(lock_doc, sort_keys=True), encoding="utf-8")
    from orch_fixtures import git
    git(catalog_repo, "add", "-A")
    git(catalog_repo, "commit", "-q", "-m", "drop a bound skill")

    client.post("/session/new")
    r = client.post(f"/agents/{agent_id}/invoke", data={"task": TASK},
                    headers={"X-Forwarded-Roles": "operator"})
    assert r.status_code == 409
    assert "STALE_ENTRY" in r.text


def test_failed_registration_still_terminates_the_invocation(
        client, store, catalog_repo, monkeypatch):
    """A raising background task must not strand the invocation 'running' with
    an SSE stream that never sees terminal."""
    def boom(*_args, **_kwargs):
        raise RuntimeError("git identity missing")

    monkeypatch.setattr(assembly_mod, "assemble_and_register", boom)
    client.get("/")
    client.post("/tasks", data={"task": TASK})

    iid = last_invocation(store, client)
    events = store.events_after(iid, 0)
    assert events[-1].event_type == "terminal"
    assert events[-1].data["status"] == "ERROR"
    assert "git identity missing" in events[-1].data["result_ref"]
    assert store.get_invocation(iid).status == "error"


def test_failed_registration_leaves_no_partial_dir(client, store, catalog_repo,
                                                   settings, monkeypatch):
    """A partial agent dir would permanently satisfy the dedup check for an
    agent no lock contains."""
    import orchestrator.assembly as mod

    real_write = mod._write_registration

    def fail_after_write(agent_dir, *args, **kwargs):
        real_write(agent_dir, *args, **kwargs)
        raise RuntimeError("lockbuild blew up")

    monkeypatch.setattr(mod, "_write_registration", fail_after_write)
    client.get("/")
    client.post("/tasks", data={"task": TASK})
    assert b1_dirs(catalog_repo) == [], "partial registration must be rolled back"


def test_meta_pages_persist_their_session(client, store):
    """Entering via /agents must mint one session, not one per request."""
    r = client.get("/agents", headers={"X-Forwarded-Roles": "operator"})
    assert r.status_code == 200
    from orchestrator.webapp import SESSION_COOKIE
    assert SESSION_COOKIE in r.cookies

    before = len(store._query("SELECT session_id FROM session"))
    client.get("/agents", headers={"X-Forwarded-Roles": "operator"})
    client.get("/agents", headers={"X-Forwarded-Roles": "operator"})
    after = len(store._query("SELECT session_id FROM session"))
    assert after == before, "existing session cookie must be reused"


def test_stale_best_match_does_not_shadow_a_healthy_agent(client, store, catalog_repo):
    """A stale higher-scorer is refused individually; the cascade still sees
    the next candidate rather than skipping the agent stage entirely."""
    client.get("/")
    client.post("/tasks", data={"task": TASK})
    agent_id = b1_dirs(catalog_repo)[0].name

    impl = catalog_repo / "skills" / "es_search" / "impl" / "run.py"
    impl.write_text(impl.read_text() + "\n# drifted\n", encoding="utf-8")
    rebuild_and_commit(catalog_repo, "drift es_search")

    client.post("/session/new")
    client.post("/tasks", data={"task": TASK})
    steps = routing_steps(store, last_invocation(store, client))
    stale = [s for s in steps if "STALE_ENTRY" in s.get("note", "")]
    assert stale and stale[0]["entry_id"] == agent_id
    # the refusal did not end the cascade
    assert any(s["cascade_step"] in {"b1-coverage", "unrecognized"} for s in steps)


def test_a_broken_scorer_refuses_the_task_instead_of_mis_routing(settings, store):
    """The scorer gates every cascade step. If it cannot answer, rounding it
    down to 0.0 is a routing decision: below match_threshold the cascade falls
    through and records a PATTERN_UNRECOGNIZED, poisoning the B2 backlog signal
    with a gap that does not exist. Refuse the invocation instead."""
    from fastapi.testclient import TestClient

    from orch_fixtures import fake_invoker
    from orchestrator.scoring import ScorerError
    from orchestrator.webapp import create_app

    def broken(task_text: str, candidate_text: str) -> float:
        raise ScorerError("scorer returned empty content")

    client = TestClient(create_app(settings, store=store, scorer=broken,
                                   invoke_tool=fake_invoker))
    client.get("/")
    r = client.post("/tasks", data={"task": TASK})

    assert r.status_code == 503
    assert not store.error_records(), "no routing decision was made — record none"
    assert store.get_invocation(last_invocation(store, client)).status == "error"


SPEC_WRAPPED = """# SPEC — cron-next-fire-times (B2b, full-agent)

## 1. Capability restatement (§5.1)

Given a cron expression and a start timestamp, the agent returns the next five fire
times of that schedule, expressed in UTC. The start timestamp is normalised to UTC
(a naive timestamp is read as UTC, an offset-bearing one is converted); fire times
are strictly after it, ascending.

## 2. Something else

Not part of the summary.
"""


def test_summary_writer_does_not_publish_a_wrapped_fragment():
    """CATALOG §4 wants 1-2 sentences and the router embeds this text. Reading
    the first physical line of hard-wrapped SPEC prose cut mid-sentence, so the
    promoted entry shipped '...returns the next five fire' as routing data."""
    from orchestrator.webapp import stub_summary_writer

    summary = stub_summary_writer(SPEC_WRAPPED)

    assert summary.startswith("Given a cron expression")
    assert summary.endswith(".")
    assert "next five fire times of that schedule" in summary
    assert "Not part of the summary" not in summary
    assert 1 <= summary.count(".") <= 3  # 1-2 sentences (abbrev-free prose)


def test_summary_writer_falls_back_when_the_spec_has_no_prose():
    from orchestrator.webapp import stub_summary_writer

    assert stub_summary_writer("# heading only\n\n") == "Certified capability."
