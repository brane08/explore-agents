"""CHECKLISTS layer 3 [M] — routing over the pinned-epoch lock."""
from __future__ import annotations

from conftest import GIBBERISH, TASK, add_skill, demote_all, rebuild_and_commit


def routing_steps(store, invocation_id):
    return [e.data for e in store.events_after(invocation_id, 0)
            if e.event_type == "routing"]


def last_invocation(store, client):
    # invocations are per-session; grab the newest via the trace
    return store._query(
        "SELECT invocation_id FROM invocation ORDER BY created_at DESC, rowid DESC"
    )[0][0]


def test_router_input_is_the_pinned_lock_only(client, store, catalog_repo):
    client.get("/")                                   # pins the session epoch
    task = "translate this document text into pig latin"
    add_skill(catalog_repo, "pig-latin",
              "Translate document text into pig latin.")
    from conftest import grant_validated
    grant_validated(catalog_repo, "pig-latin")
    rebuild_and_commit(catalog_repo, "add pig-latin")

    # pinned session: the new entry does not exist for this router
    r = client.post("/tasks", data={"task": task})
    assert r.status_code == 200
    assert "PATTERN_UNRECOGNIZED" in r.text

    # a fresh session pins the new epoch and finds coverage
    client.post("/session/new")
    r = client.post("/tasks", data={"task": task})
    assert "PATTERN_UNRECOGNIZED" not in r.text
    steps = routing_steps(store, last_invocation(store, client))
    assert any(s["cascade_step"] == "b1-coverage" and "pig-latin" in s.get("note", "")
               for s in steps)


def test_cascade_order_and_structured_error(client, store):
    client.get("/")
    r = client.post("/tasks", data={"task": GIBBERISH})
    assert r.status_code == 200
    assert "PATTERN_UNRECOGNIZED" in r.text
    steps = routing_steps(store, last_invocation(store, client))
    order = [s["cascade_step"] for s in steps]
    # agent → template → coverage → unrecognized, no automatic escalation
    assert order[-1] == "unrecognized"
    assert "template-match" in order
    # terminal ERROR persisted; nothing was invoked
    events = store.events_after(last_invocation(store, client), 0)
    assert events[-1].event_type == "terminal"
    assert events[-1].data["status"] == "ERROR"
    assert not any(e.event_type == "tool_call" for e in events)


def test_residual_hygiene_strips_raw_user_text(client, store):
    client.get("/")
    client.post("/tasks", data={
        "task": 'email bob@corp.example the "Q3 secret plan 1234" immediately'})
    records = store.error_records()
    assert records, "PATTERN_UNRECOGNIZED must be persisted"
    code, residual = records[-1]
    assert code == "PATTERN_UNRECOGNIZED"
    assert residual.startswith("capability needed:")
    assert "@" not in residual and "1234" not in residual and '"' not in residual


def test_recall_pierces_the_epoch_pin(client, store, catalog_repo, settings):
    client.get("/")
    # first use registers a validated B1 assembly
    r = client.post("/tasks", data={"task": TASK})
    assert "PATTERN_UNRECOGNIZED" not in r.text

    # new session routes the repeat task to the registration
    client.post("/session/new")
    r = client.post("/tasks", data={"task": TASK})
    steps = routing_steps(store, last_invocation(store, client))
    assert any(s["cascade_step"] == "agent-match" and "confirmation_score" in s
               for s in steps)

    # demote the tools in live trust.yaml — same session, same pinned lock:
    # the very next invocation must refuse (no lock rebuild involved)
    demote_all(catalog_repo)
    r = client.post("/tasks", data={"task": TASK})
    assert "STALE_ENTRY" in r.text
    assert any("tier:" in reason for _, reason in [(e, r2) for e, r2 in store.stale_queue()])


def test_operator_authorization_joins_tiers(client, store, catalog_repo, settings):
    client.get("/")
    client.post("/tasks", data={"task": TASK})          # registers b1 agent
    demote_all(catalog_repo)                            # everything quarantined
    client.post("/session/new")

    # plain user: refused
    r = client.post("/tasks", data={"task": TASK})
    assert "STALE_ENTRY" in r.text

    # operator (gateway-injected identity): quarantined is within allowed tiers
    r = client.post("/tasks", data={"task": TASK},
                    headers={"X-Forwarded-User": "op@example.com"})
    assert "STALE_ENTRY" not in r.text


def test_stale_lock_entry_is_refused_and_queued(client, store, catalog_repo):
    client.get("/")
    client.post("/tasks", data={"task": TASK})          # registers b1 agent

    # drift a bound skill => the registered agent goes stale in the new lock
    impl = catalog_repo / "skills" / "es_search" / "impl" / "run.py"
    impl.write_text(impl.read_text() + "\n# drifted\n", encoding="utf-8")
    rebuild_and_commit(catalog_repo, "drift es_search")

    client.post("/session/new")                         # pin the stale lock
    r = client.post("/tasks", data={"task": TASK})
    steps = routing_steps(store, last_invocation(store, client))
    stale_notes = [s for s in steps if "STALE_ENTRY" in s.get("note", "")]
    assert stale_notes, "stale agent match must be refused with STALE_ENTRY"
    assert store.stale_queue()
    # cascade continued (hard refusal of the entry, not of the task)
    assert any(s["cascade_step"] in {"b1-coverage", "unrecognized"} for s in steps)
