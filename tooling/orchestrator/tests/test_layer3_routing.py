"""CHECKLISTS layer 3 [M] — routing over the pinned-epoch lock."""
from __future__ import annotations

from orch_fixtures import GIBBERISH, TASK, add_skill, demote_all, rebuild_and_commit
from orchestrator.router import route
from orchestrator.scoring import lexical_scorer


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
    from orch_fixtures import grant_validated
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


def test_stale_tool_is_refused_from_b1_coverage(catalog_repo, settings, store):
    """CHECKLISTS layer 3 [M] 'stale entries refused' is unqualified — it must
    hold for B1 tool-coverage candidates (skills/mcp-tools), not just agent
    matches. Build the lock normally, then hand-flag one covering tool stale
    (as lockbuild would for a kind that carries manifest bindings) and confirm
    the router excludes it rather than silently assembling it in."""
    from lockbuild.build import build_lock

    lock = build_lock(catalog_repo)
    es_search = next(e for e in lock["entries"] if e["id"] == "es_search")
    es_search["stale"] = True
    es_search["stale_reason"] = "binding-drift"

    result = route(TASK, lock, catalog_repo, "user@example.com",
                    lexical_scorer, settings, store)

    stale_notes = [e for e in result.events
                   if e.cascade_step == "b1-coverage" and "STALE_ENTRY" in e.note]
    assert stale_notes and stale_notes[0].entry_id == "es_search"
    assert ("es_search", "binding-drift") in store.stale_queue()
    if result.outcome == "assemble-b1":
        assert all(t["id"] != "es_search" for t in result.tools)


def test_operator_authorization_joins_tiers(client, store, catalog_repo, settings):
    client.get("/")
    client.post("/tasks", data={"task": TASK})          # registers b1 agent
    demote_all(catalog_repo)                            # everything quarantined
    client.post("/session/new")

    # plain user: refused at the tier check, never reaches quarantined dispatch
    r = client.post("/tasks", data={"task": TASK})
    assert "refused at live tier" in r.text

    # operator (gateway-injected identity): quarantined is within allowed
    # tiers, so the operator reaches supervised dispatch instead of the
    # tier-refusal (Fix 1: the /tasks cascade applies the same layer-8
    # supervision gate as the direct invoke route) — refused here for lack
    # of an evidence path (no counterpart, no canary opt-in), not for tier
    # authorization.
    r = client.post("/tasks", data={"task": TASK},
                    headers={"X-Forwarded-User": "op@example.com"})
    assert "refused at live tier" not in r.text
    assert "supervised invocation unavailable" in r.text


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


def test_routing_scores_the_catalog_in_parallel_not_one_entry_at_a_time(
        catalog_repo, settings, store):
    """Every entry in the cascade is an independent scorer call, and against a
    model-backed scorer each one is a network round trip. Serially, routing
    latency grows with the catalog; the cascade's *decisions* are unchanged
    either way, so the calls belong in flight together."""
    import threading

    from lockbuild.build import build_lock

    lock = build_lock(catalog_repo)
    in_flight, peak, guard = 0, 0, threading.Lock()

    def scorer(task_text: str, candidate_text: str) -> float:
        nonlocal in_flight, peak
        with guard:
            in_flight += 1
            peak = max(peak, in_flight)
        try:
            import time
            time.sleep(0.01)
            return lexical_scorer(task_text, candidate_text)
        finally:
            with guard:
                in_flight -= 1

    result = route(TASK, lock, catalog_repo, "user@example.com",
                   scorer, settings, store)
    assert result.outcome != "error"
    assert peak > 1
