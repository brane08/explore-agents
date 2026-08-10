"""CHECKLISTS layer 4 [M] — B1 assembly & registration (+ Phase 1 exit criteria)."""
from __future__ import annotations

import yaml

from orch_fixtures import TASK
from lockbuild.build import build_lock
from orchestrator.assembly import assembly_config, config_hash
from test_layer3_routing import last_invocation, routing_steps


def b1_dirs(catalog_repo):
    return sorted(p for p in (catalog_repo / "agents").iterdir()
                  if p.name.startswith("b1-"))


def test_first_successful_use_registers(client, store, catalog_repo, settings):
    client.get("/")
    r = client.post("/tasks", data={"task": TASK})
    assert r.status_code == 200

    events = store.events_after(last_invocation(store, client), 0)
    types = [e.event_type for e in events]
    assert "invocation" in types and "tool_call" in types
    assert events[-2].data.get("status") == "COMPLETE" or any(
        e.event_type == "terminal" and e.data["status"] == "COMPLETE" for e in events)

    dirs = b1_dirs(catalog_repo)
    assert len(dirs) == 1
    agent_dir = dirs[0]
    # generated prompt stored as a versioned file
    prompt = (agent_dir / "prompts" / "default.md").read_text(encoding="utf-8")
    assert "Tools:" in prompt
    # registration skipped _proposed/
    assert not any((catalog_repo / "agents" / "_proposed").glob("b1-*"))
    # manifest records bindings with hashes; entry carries assembly: b1
    entry = yaml.safe_load((agent_dir / "entry.yaml").read_text(encoding="utf-8"))
    assert entry["assembly"] == "b1"
    manifest = yaml.safe_load((agent_dir / "AGENT_MANIFEST.yaml").read_text(encoding="utf-8"))
    assert all(b["schema_hash"] for b in manifest["bindings"])
    # registration was committed so new epochs can route to it
    assert (catalog_repo / "agents" / agent_dir.name / "entry.yaml").exists()


def test_registration_tier_is_min_of_bindings(client, store, catalog_repo, settings):
    client.get("/")
    client.post("/tasks", data={"task": TASK})
    agent_id = b1_dirs(catalog_repo)[0].name
    lock = build_lock(catalog_repo, built_from="0" * 40, built_at="x")
    entry = next(e for e in lock["entries"] if e["id"] == agent_id)
    assert entry["trust_tier"] == "validated"     # all bindings validated
    assert entry["kind"] == "agent"


def test_identity_is_config_hash_and_deduplicates(client, store, catalog_repo, settings):
    client.get("/")
    client.post("/tasks", data={"task": TASK})
    client.post("/tasks", data={"task": TASK})    # same pinned lock → assembles again
    dirs = b1_dirs(catalog_repo)
    assert len(dirs) == 1, "identical config must deduplicate, not re-register"
    steps = routing_steps(store, last_invocation(store, client))
    assert any(s["cascade_step"] == "b1-deduplicated" for s in steps)

    # the directory name is derived from sha256(config)
    manifest = yaml.safe_load((dirs[0] / "AGENT_MANIFEST.yaml").read_text(encoding="utf-8"))
    src_config = yaml.safe_load((dirs[0] / "src" / "config.yaml").read_text(encoding="utf-8"))
    assert dirs[0].name == f"b1-{config_hash(src_config)[:12]}"
    assert manifest["config_hash"] == config_hash(src_config)


def test_repeat_task_reroutes_to_registration(client, store, catalog_repo):
    """Phase 1 exit criteria: routes → streams → registers → re-routes on repeat."""
    client.get("/")
    client.post("/tasks", data={"task": TASK})
    agent_id = b1_dirs(catalog_repo)[0].name

    client.post("/session/new")                   # new epoch includes the registration
    r = client.post("/tasks", data={"task": TASK})
    assert r.status_code == 200
    iid = last_invocation(store, client)
    steps = routing_steps(store, iid)
    match = next(s for s in steps if s["cascade_step"] == "agent-match")
    assert match["entry_id"] == agent_id
    assert match["confirmation_score"] >= 0.5
    events = store.events_after(iid, 0)
    assert any(e.event_type == "terminal" and e.data["status"] == "COMPLETE"
               for e in events)
    assert store.get_invocation(iid).agent_id == agent_id
