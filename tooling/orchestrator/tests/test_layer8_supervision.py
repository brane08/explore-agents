"""CHECKLISTS layer 8 [M] — supervised invocation and the promotion streak."""
from __future__ import annotations

import re

import pytest

from orchestrator.store import SQLiteStore

KEY = dict(entry_id="cron-next-fire-times", entry_version="0.1.0",
           model_profile="stub-class-ref")


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(tmp_path / "orch.sqlite3")


def _clean_run(store, n: int) -> None:
    for i in range(n):
        rid = store.open_supervised_run(**KEY, invocation_id=f"inv{i}", mode="shadow")
        store.close_supervised_run(rid, verdict="clean")


def test_streak_counts_consecutive_clean_runs(store):
    _clean_run(store, 3)
    assert store.supervised_streak(**KEY) == 3


def test_incident_resets_the_streak_to_zero(store):
    _clean_run(store, 3)
    rid = store.open_supervised_run(**KEY, invocation_id="bad", mode="shadow")
    store.close_supervised_run(rid, verdict="incident", reason="field 'count' differs")

    assert store.supervised_streak(**KEY) == 0


def test_streak_resumes_after_an_incident(store):
    rid = store.open_supervised_run(**KEY, invocation_id="bad", mode="shadow")
    store.close_supervised_run(rid, verdict="incident", reason="x")
    _clean_run(store, 2)

    assert store.supervised_streak(**KEY) == 2


def test_a_voided_run_counts_for_neither_streak_nor_reset(store):
    _clean_run(store, 2)
    rid = store.open_supervised_run(**KEY, invocation_id="void", mode="shadow")
    store.close_supervised_run(rid, verdict="void", reason="tool plane down",
                               adjudicated_by="op@example.com")
    _clean_run(store, 1)

    # 2 before + 1 after; the void neither reset nor incremented
    assert store.supervised_streak(**KEY) == 3


def test_a_pending_run_does_not_count_toward_the_streak(store):
    _clean_run(store, 2)
    store.open_supervised_run(**KEY, invocation_id="open", mode="canary")

    assert store.supervised_streak(**KEY) == 2
    assert [r.invocation_id for r in store.pending_supervised_runs()] == ["open"]


def test_evidence_never_crosses_a_version_or_profile_change(store):
    _clean_run(store, 5)

    bumped = dict(KEY, entry_version="0.2.0")
    migrated = dict(KEY, model_profile="other-class-ref")

    assert store.supervised_streak(**bumped) == 0
    assert store.supervised_streak(**migrated) == 0
    assert store.supervised_streak(**KEY) == 5


# Mode selection tests

from orchestrator.supervise import SupervisionPlan, is_read_only, select_mode

TYPED = {"id": "cron-next-fire-times", "version": "0.1.0",
         "model_requirements": {"structured_output": True}}
PROSE = {"id": "summarizer", "version": "1.0.0",
         "model_requirements": {"structured_output": False}}
COUNTERPART = {"id": "cron-legacy", "version": "2.0.0"}


def test_typed_output_with_a_counterpart_selects_shadow():
    plan = select_mode(TYPED, counterpart=COUNTERPART, read_only_bindings=set(),
                       canary_opt_in=set())
    assert plan.mode == "shadow"
    assert plan.counterpart == COUNTERPART


def test_prose_output_never_selects_shadow_even_with_a_counterpart():
    """Divergence is only mechanically defined over typed fields (design §2.2)."""
    plan = select_mode(PROSE, counterpart=COUNTERPART, read_only_bindings=set(),
                       canary_opt_in=set())
    assert plan.mode == "canary"


def test_no_counterpart_falls_back_to_canary_for_a_binding_free_agent():
    plan = select_mode(TYPED, counterpart=None, read_only_bindings=set(),
                       canary_opt_in=set())
    assert plan.mode == "canary"


def test_write_capable_agent_without_opt_in_is_refused_not_canaried():
    entry = dict(TYPED, bindings=[{"id": "db-writer", "version": "1.0.0"}])
    plan = select_mode(entry, counterpart=None, read_only_bindings=set(),
                       canary_opt_in=set())
    assert plan.mode == "refuse"
    assert "canary opt-in" in plan.reason


def test_write_capable_agent_with_opt_in_is_canaried():
    entry = dict(TYPED, bindings=[{"id": "db-writer", "version": "1.0.0"}])
    plan = select_mode(entry, counterpart=None, read_only_bindings=set(),
                       canary_opt_in={"cron-next-fire-times"})
    assert plan.mode == "canary"


def test_read_only_bindings_keep_an_agent_canary_eligible():
    entry = dict(TYPED, bindings=[{"id": "log-search", "version": "1.0.0"}])
    plan = select_mode(entry, counterpart=None,
                       read_only_bindings={"log-search"}, canary_opt_in=set())
    assert plan.mode == "canary"


def test_refusal_reason_names_the_failed_precondition():
    entry = dict(PROSE, bindings=[{"id": "db-writer", "version": "1.0.0"}])
    plan = select_mode(entry, counterpart=None, read_only_bindings=set(),
                       canary_opt_in=set())
    assert plan.mode == "refuse"
    assert plan.reason  # non-empty, names why no evidence path exists


def test_binding_free_agent_is_read_only():
    assert is_read_only(TYPED, set()) is True


def test_supervision_threshold_defaults_to_twenty(monkeypatch):
    from orchestrator.config import settings_from_env
    monkeypatch.setenv("ORCH_SUPERVISION_THRESHOLD", "")
    assert settings_from_env().supervision_threshold == 20


# Field-predicate divergence comparison tests

from orchestrator.supervise import FieldPredicate, compare_outputs


def test_identical_typed_output_is_clean():
    verdict, reason = compare_outputs({"count": 5}, {"count": 5}, {})
    assert verdict == "clean"
    assert reason == ""


def test_default_predicate_is_exact_match():
    verdict, reason = compare_outputs({"count": 5}, {"count": 6}, {})
    assert verdict == "incident"
    assert "count" in reason


def test_numeric_tolerance_absorbs_benign_variation():
    predicates = {"elapsed": FieldPredicate("numeric", tolerance=0.5)}
    verdict, _ = compare_outputs({"elapsed": 1.2}, {"elapsed": 1.0}, predicates)
    assert verdict == "clean"


def test_numeric_tolerance_still_catches_a_real_difference():
    predicates = {"elapsed": FieldPredicate("numeric", tolerance=0.5)}
    verdict, reason = compare_outputs({"elapsed": 9.0}, {"elapsed": 1.0}, predicates)
    assert verdict == "incident"
    assert "elapsed" in reason


def test_set_predicate_ignores_order_where_order_is_not_contractual():
    predicates = {"tags": FieldPredicate("set")}
    verdict, _ = compare_outputs({"tags": ["b", "a"]}, {"tags": ["a", "b"]}, predicates)
    assert verdict == "clean"


def test_a_missing_field_is_an_incident():
    verdict, reason = compare_outputs({}, {"count": 5}, {})
    assert verdict == "incident"
    assert "count" in reason


def test_an_extra_field_is_an_incident():
    verdict, reason = compare_outputs({"count": 5, "extra": 1}, {"count": 5}, {})
    assert verdict == "incident"
    assert "extra" in reason


def test_the_reason_names_the_first_failing_field_deterministically():
    verdict, reason = compare_outputs({"a": 1, "b": 1}, {"a": 2, "b": 2}, {})
    assert verdict == "incident"
    assert reason.startswith("field 'a'")  # sorted, not dict-order dependent


# run_shadow / open_canary recorders (Task 4 Step 1)

from orchestrator.supervise import open_canary, run_shadow


def test_shadow_records_clean_when_outputs_agree(store):
    rid = run_shadow(store, entry=TYPED, model_profile="stub-class-ref",
                     invocation_id="i1", counterpart_id="cron-legacy",
                     counterpart_output={"count": 5}, shadow_output={"count": 5},
                     error=None, predicates={})
    run = store.supervised_runs("cron-next-fire-times")[0]
    assert (run.run_id, run.verdict, run.mode) == (rid, "clean", "shadow")
    assert run.counterpart_id == "cron-legacy"


def test_shadow_records_incident_when_the_quarantined_agent_crashes(store):
    """A crash is attributable to the agent — it is evidence, not noise."""
    run_shadow(store, entry=TYPED, model_profile="stub-class-ref",
               invocation_id="i1", counterpart_id="cron-legacy",
               counterpart_output={"count": 5}, shadow_output=None,
               error="ZeroDivisionError: division by zero", predicates={})
    run = store.supervised_runs("cron-next-fire-times")[0]
    assert run.verdict == "incident"
    assert "ZeroDivisionError" in run.reason


def test_counterpart_error_auto_voids_the_run(store):
    """Nothing to compare against: the run did not measure the agent."""
    run_shadow(store, entry=TYPED, model_profile="stub-class-ref",
               invocation_id="i1", counterpart_id="cron-legacy",
               counterpart_output=None, shadow_output={"count": 5},
               error=None, predicates={})
    run = store.supervised_runs("cron-next-fire-times")[0]
    assert run.verdict == "void"
    assert run.reason == "counterpart_error"


def test_canary_opens_a_pending_row_for_human_adjudication(store):
    rid = open_canary(store, entry=TYPED, model_profile="stub-class-ref",
                      invocation_id="i9")
    run = store.supervised_runs("cron-next-fire-times")[0]
    assert (run.run_id, run.verdict, run.mode) == (rid, "pending", "canary")


# Supervised dispatch — invoke_agent route wiring (Task 4 Step 5)

from fastapi.testclient import TestClient

from orch_fixtures import add_agent, grant_tier, rebuild_and_commit
from orchestrator.config import Settings
from orchestrator.scoring import lexical_scorer
from orchestrator.webapp import create_app

_READ_ONLY_BINDING = "es_search"


def _supervised_invoker(tool_id: str, args: dict) -> dict:
    if tool_id == "es_aggregate":
        return {"marker": "COUNTERPART-OUTPUT"}
    return {"marker": "SHADOW-OUTPUT"}


@pytest.fixture
def supervised_settings(catalog_repo, tmp_path):
    """Own settings/store/client trio (not the `client`/`store` names from
    orch_fixtures) — this module already shadows `store` with the bare
    KEY-based fixture above for the unit tests, so the catalog-backed
    integration tests use distinctly-named fixtures instead of colliding
    with it."""
    add_agent(catalog_repo, "counterpart-agent",
              "Count the things in a batch and report the total.",
              bindings=[{"id": "es_aggregate", "version": "1.0.0"}])
    grant_tier(catalog_repo, "counterpart-agent", "validated")

    add_agent(catalog_repo, "quarantined-agent",
              "Count the things in a batch and report the total.",
              bindings=[{"id": "es_search", "version": "1.0.0"}])
    grant_tier(catalog_repo, "quarantined-agent", "quarantined")

    add_agent(catalog_repo, "write-capable-agent", "Do a thing that writes state.",
              bindings=[{"id": "es_aggregate", "version": "1.0.0"}])
    grant_tier(catalog_repo, "write-capable-agent", "quarantined")

    rebuild_and_commit(catalog_repo, "add layer 8 supervision fixtures")
    return Settings(
        catalog_root=catalog_repo, db_path=tmp_path / "sup-orch.sqlite3",
        secret_key="test-secret", operator_users=("op@example.com",),
        read_only_bindings=frozenset({_READ_ONLY_BINDING}),
    )


@pytest.fixture
def supervised_store(supervised_settings):
    return SQLiteStore(supervised_settings.db_path)


@pytest.fixture
def supervised_client(supervised_settings, supervised_store):
    app = create_app(supervised_settings, store=supervised_store,
                     scorer=lexical_scorer, invoke_tool=_supervised_invoker)
    return TestClient(app)


def test_a_quarantined_agent_is_never_run_unsupervised(
        supervised_client, supervised_store, monkeypatch):
    """Layer 8 [M]: quarantined agents run only in supervised mode. Asserted on
    the call, not the output — an unsupervised run that happens to succeed is
    still the failure this test exists to catch."""
    calls = []
    monkeypatch.setattr("orchestrator.webapp.run_invocation",
                        lambda *a, **k: calls.append(k) or (True, "ok"))

    supervised_client.post("/agents/quarantined-agent/invoke", data={"task": "anything"})

    assert not any(c.get("supervised") is not True for c in calls)


def test_shadow_output_is_never_returned_to_the_caller(supervised_client, supervised_store):
    """Layer 8 [M]. The counterpart's answer reaches the caller; the
    quarantined agent's answer exists only in the ledger."""
    response = supervised_client.post("/agents/quarantined-agent/invoke",
                           data={"task": "count the things"})

    assert "COUNTERPART-OUTPUT" in response.text
    assert "SHADOW-OUTPUT" not in response.text
    assert supervised_store.supervised_runs("quarantined-agent")[0].mode == "shadow"


def test_refused_supervision_names_the_failed_precondition(supervised_client, supervised_store):
    response = supervised_client.post("/agents/write-capable-agent/invoke",
                           data={"task": "do a thing"})

    assert "STALE_ENTRY" in response.text
    assert "no evidence path" in response.text
    assert supervised_store.supervised_runs("write-capable-agent") == []


# Canary review screen (Task 5)


def test_canary_screen_is_operator_gated(client):
    """Layer 10 [M]: meta screens gated by operator role."""
    assert client.get("/canary/quarantined-agent").status_code == 403


def test_operator_adjudication_closes_the_row_and_records_who(operator_client, store):
    rid = open_canary(store, entry=TYPED, model_profile="stub-class-ref",
                      invocation_id="i1")

    operator_client.post("/canary/cron-next-fire-times/adjudicate",
                         data={"run_id": rid, "verdict": "clean"})

    run = store.supervised_runs("cron-next-fire-times")[0]
    assert run.verdict == "clean"
    assert run.adjudicated_by == "op@example.com"


def test_voiding_records_the_reason(operator_client, store):
    rid = open_canary(store, entry=TYPED, model_profile="stub-class-ref",
                      invocation_id="i1")

    operator_client.post("/canary/cron-next-fire-times/void",
                         data={"run_id": rid, "reason": "tool plane down"})

    run = store.supervised_runs("cron-next-fire-times")[0]
    assert (run.verdict, run.reason) == ("void", "tool plane down")


def test_demote_redirects_instead_of_re_rendering(operator_client, store):
    """Fix round 1: a 200 inline render here made a page refresh resubmit the
    POST, calling `queue_stale` again with no uniqueness constraint —
    duplicate `stale_queue` rows. PRG (redirect-then-GET) closes that."""
    response = operator_client.post(
        "/canary/cron-next-fire-times/demote", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/canary/cron-next-fire-times?demoted=1"
    assert len(store.stale_queue()) == 1

    # A GET of the redirect target still shows the flash banner, sourced from
    # the query param rather than persistent store state.
    followed = operator_client.get(response.headers["location"])
    assert followed.status_code == 200
    assert "demot" in followed.text.lower()


@pytest.fixture
def streak_settings(catalog_repo, tmp_path):
    """Own settings/store/client trio so `cron-next-fire-times` resolves as a
    real, routable lock entry (matching KEY's entry_id/entry_version/
    model_profile) when the GET route's `entry_by_id(_current_lock(), ...)`
    looks it up — the shared `operator_client`/`settings` fixtures never add
    such an entry, so the route's `entry` fallback would otherwise mask the
    real streak with 0. See orch_fixtures.add_agent/grant_tier, and Task 4's
    supervised_settings fixture above, which seeds entries the same way."""
    add_agent(catalog_repo, KEY["entry_id"], "Compute the next N cron fire times.",
              version=KEY["entry_version"], model_profile=KEY["model_profile"])
    grant_tier(catalog_repo, KEY["entry_id"], "quarantined",
              profile=KEY["model_profile"], version=KEY["entry_version"])
    rebuild_and_commit(catalog_repo, "add streak-screen fixture entry")
    return Settings(
        catalog_root=catalog_repo, db_path=tmp_path / "streak-orch.sqlite3",
        secret_key="test-secret", operator_users=("op@example.com",),
    )


@pytest.fixture
def streak_store(streak_settings):
    return SQLiteStore(streak_settings.db_path)


@pytest.fixture
def streak_client(streak_settings, streak_store):
    app = create_app(streak_settings, store=streak_store,
                     scorer=lexical_scorer, invoke_tool=_supervised_invoker)
    test_client = TestClient(app)
    test_client.headers.update({"X-Forwarded-User": "op@example.com",
                                "X-Forwarded-Roles": "operator"})
    return test_client


def test_screen_shows_the_streak_and_whether_the_key_is_eligible(
        streak_client, streak_store):
    for i in range(2):
        rid = streak_store.open_supervised_run(**KEY, invocation_id=f"i{i}", mode="canary")
        streak_store.close_supervised_run(rid, verdict="clean", adjudicated_by="op@example.com")

    body = streak_client.get("/canary/cron-next-fire-times").text

    # Precise check on the rendered streak value itself, not an incidental
    # substring of the threshold (e.g. "20" containing "2").
    m = re.search(r"streak[^0-9]{0,40}?(\d+)", body, re.IGNORECASE)
    assert m is not None and m.group(1) == "2", body
    assert "not eligible" in body.lower()  # threshold is 20
