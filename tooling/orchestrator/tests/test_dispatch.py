"""B2b dispatch — Role A invocation (ROADMAP §3).

What dispatch owes the platform: the §0 inputs assembled correctly, the role
boundary held (ROADMAP §0), unlisted harnesses refused before they run
(CATALOG §10 / layer 7 step 1), and *nothing promoted* — a candidate lands in
`_proposed/` and stops there until a human certifies it.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from certify.candidate import load_candidate
from certify.contract import check_contract, contract_passed
from certify.conformance import HarnessInputs, HarnessOutcome

from orchestrator.config import Settings
from orchestrator.dispatch import (
    PROPOSED_DIR,
    TRUST_TIER_CEILING,
    build_inputs,
    candidate_id,
    dispatch,
    dispatch_b2b,
    select_harness,
    stub_harness,
)

HARNESS = "stub-harness/0"

LOCK = {
    "lock_version": 3,
    "built_from": "0" * 40,
    "entries": [
        {"id": "es_search", "kind": "skill", "version": "1.0.0", "hash": "aaa",
         "trust_tier": "validated", "capability_tags": ["log-source"],
         "routing_summary": "Search log records by level.", "detail": ""},
        {"id": "field_stats", "kind": "mcp-tool", "version": "1.0.0", "hash": "ccc",
         "trust_tier": "quarantined", "capability_tags": ["log-analysis"],
         "routing_summary": "Field statistics.", "detail": ""},
        {"id": "shipped-agent", "kind": "agent", "version": "1.0.0", "hash": "ddd",
         "trust_tier": "validated", "capability_tags": [],
         "routing_summary": "An existing agent.", "detail": ""},
    ],
}

TASK = "list error-level log records for a service"
CRITERIA = "BC-1: returns only records whose level equals ERROR.\n"


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    (tmp_path / "conformance.yaml").write_text(yaml.safe_dump({
        "harnesses": [{"harness": HARNESS, "conformant": True,
                       "recorded_at": "2026-07-16T00:00:00+00:00"}],
    }), encoding="utf-8")
    return tmp_path


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(catalog_root=tmp_path, db_path=tmp_path / "o.sqlite3")


def _inputs(settings, **kw):
    return build_inputs(TASK, CRITERIA, "a" * 40, LOCK, settings, HARNESS, **kw)


# --- §0 input assembly ------------------------------------------------------

def test_inputs_carry_the_epoch_pin_and_ceiling(settings):
    inputs = _inputs(settings)
    assert inputs.catalog_ref == "a" * 40
    assert inputs.capability_manifest is LOCK       # the lock AT catalog_ref
    assert inputs.trust_tier_ceiling == TRUST_TIER_CEILING == "validated"
    assert inputs.mode == "B2b"
    assert inputs.missing() == []


def test_bindable_subset_excludes_agents_and_sub_ceiling_entries(settings):
    bindable = {e["id"] for e in _inputs(settings).bindable()}
    assert bindable == {"es_search"}, "only validated skill|mcp-tool may be bound"


def test_delta_scope_without_residual_is_a_missing_input(settings, catalog):
    result = dispatch(_inputs(settings, scope="delta"), catalog, stub_harness)
    assert result.outcome == "refused"
    assert result.error.code == "MISSING_INPUT"
    assert "RESIDUAL" in result.refusal


# --- CATALOG §10: conformance is measured, never assumed --------------------

def test_unlisted_harness_is_refused_before_the_harness_runs(settings, tmp_path):
    def never(inputs, out):
        raise AssertionError("an unlisted harness must not be invoked")

    result = dispatch(_inputs(settings), tmp_path, never)
    assert result.outcome == "refused"
    assert "not on the conformance list" in result.refusal


def test_absent_conformance_list_lists_nobody(settings, tmp_path):
    result = dispatch(_inputs(settings), tmp_path, stub_harness)
    assert result.outcome == "refused"


def test_non_conformant_record_does_not_list_the_harness(settings, catalog):
    (catalog / "conformance.yaml").write_text(yaml.safe_dump({
        "harnesses": [{"harness": HARNESS, "conformant": False}],
    }), encoding="utf-8")
    result = dispatch(_inputs(settings), catalog, stub_harness)
    assert result.outcome == "refused"
    assert "not on the conformance list" in result.refusal


# --- the candidate ----------------------------------------------------------

def test_candidate_lands_in_proposed_and_is_never_promoted(settings, catalog):
    result = dispatch(_inputs(settings), catalog, stub_harness)
    assert result.outcome == "candidate"
    assert result.candidate_dir.parent == catalog / PROPOSED_DIR
    assert result.candidate_dir.name == candidate_id(TASK, CRITERIA, "a" * 40)
    # promotion is a human act through certify — dispatch touches none of it
    assert not (catalog / "agents" / result.candidate_dir.name).exists()
    assert not (catalog / "trust.yaml").exists()
    assert not (catalog / "catalog.lock.yaml").exists()


def test_stub_candidate_satisfies_the_contract_it_is_dispatched_under(settings, catalog):
    """The stub is a stand-in, but a stand-in that violated §7 would make every
    downstream test meaningless."""
    inputs = _inputs(settings)
    result = dispatch(inputs, catalog, stub_harness)
    candidate = load_candidate(result.candidate_dir)
    results = check_contract(candidate, inputs)
    assert contract_passed(results), [(r.item, r.detail) for r in results if not r.passed]


def test_stub_reports_partial_because_it_implements_no_behavior(settings, catalog):
    result = dispatch(_inputs(settings), catalog, stub_harness)
    assert result.harness_outcome.status == "PARTIAL"
    manifest = yaml.safe_load(
        (result.candidate_dir / "AGENT_MANIFEST.yaml").read_text(encoding="utf-8"))
    assert manifest["status"] == "PARTIAL", "a stub claiming COMPLETE would be a false COMPLETE"


def test_same_task_at_same_epoch_reuses_the_candidate_dir(settings, catalog):
    first = dispatch(_inputs(settings), catalog, stub_harness)
    second = dispatch(_inputs(settings), catalog, stub_harness)
    assert first.candidate_dir == second.candidate_dir
    entries = list((catalog / PROPOSED_DIR).iterdir())
    assert [p for p in entries if p.is_dir()] == [first.candidate_dir]
    # plus exactly the one frozen-inputs sidecar
    assert {p.name for p in entries if p.is_file()} == {
        first.candidate_dir.name + ".inputs.yaml"}


def test_new_epoch_gets_its_own_candidate(settings, catalog):
    a = dispatch(build_inputs(TASK, CRITERIA, "a" * 40, LOCK, settings, HARNESS),
                 catalog, stub_harness)
    b = dispatch(build_inputs(TASK, CRITERIA, "b" * 40, LOCK, settings, HARNESS),
                 catalog, stub_harness)
    assert a.candidate_dir != b.candidate_dir


# --- failure handling -------------------------------------------------------

def test_harness_error_publishes_no_candidate(settings, catalog):
    def refuser(inputs, out):
        (out / "AGENT_MANIFEST.yaml").write_text("half: written\n", encoding="utf-8")
        return HarnessOutcome("ERROR", ["DELTA_INSUFFICIENT"])

    result = dispatch(_inputs(settings), catalog, refuser)
    assert result.outcome == "refused"
    assert "DELTA_INSUFFICIENT" in result.refusal
    assert not (catalog / PROPOSED_DIR).exists(), "an ERROR must leave no candidate behind"


def test_crashing_harness_leaves_no_half_candidate(settings, catalog):
    def crasher(inputs, out):
        (out / "AGENT_MANIFEST.yaml").write_text("partial\n", encoding="utf-8")
        raise RuntimeError("boom")

    result = dispatch(_inputs(settings), catalog, crasher)
    assert result.outcome == "refused"
    assert "boom" in result.refusal
    assert not (catalog / PROPOSED_DIR).exists()


def test_silent_harness_producing_nothing_is_refused(settings, catalog):
    def liar(inputs, out):
        return HarnessOutcome("COMPLETE", [])       # claims success, wrote nothing

    result = dispatch(_inputs(settings), catalog, liar)
    assert result.outcome == "refused"
    assert "no candidate produced" in result.refusal


def test_harness_warn_codes_surface_as_events(settings, catalog):
    def warner(inputs, out):
        stub_harness(inputs, out)
        return HarnessOutcome("PARTIAL", ["WARN: CRITERIA_ISSUE"], turns_used=3)

    result = dispatch(_inputs(settings), catalog, warner)
    assert result.outcome == "candidate"
    assert any(e.kind == "warn" and "CRITERIA_ISSUE" in e.note for e in result.events)


# --- the seam ---------------------------------------------------------------

def test_stub_is_the_default_backend_so_tests_need_no_credentials(monkeypatch):
    monkeypatch.delenv("ORCH_HARNESS", raising=False)
    assert select_harness() is stub_harness


def test_unknown_backend_is_rejected():
    with pytest.raises(ValueError, match="unknown harness backend"):
        select_harness("telepathy")


# --- frozen-input persistence (certify re-verification) ---------------------

def test_dispatch_persists_frozen_inputs_beside_candidate(settings, catalog):
    result = dispatch(_inputs(settings), catalog, stub_harness)
    assert result.outcome == "candidate"
    inputs_file = Path(result.candidate_dir).with_suffix(".inputs.yaml")
    assert inputs_file.is_file(), "certify needs the frozen §0 inputs to re-verify refs"
    data = yaml.safe_load(inputs_file.read_text(encoding="utf-8"))
    assert data["behavioral_criteria"] == CRITERIA
    assert data["task_spec"] == TASK
    assert data["catalog_ref"] == "a" * 40
    assert data["trust_tier_ceiling"] == "validated"
    # inputs live beside the dir — the §6 candidate layout stays exact
    assert not (Path(result.candidate_dir) / "inputs.yaml").exists()


def test_dispatch_records_who_authored_the_criteria(settings, catalog):
    result = dispatch(_inputs(settings), catalog, stub_harness,
                      criteria_authored_by="gpt-4o-mini")
    data = yaml.safe_load(
        Path(result.candidate_dir).with_suffix(".inputs.yaml").read_text(encoding="utf-8"))
    assert data["criteria_authored_by"] == "gpt-4o-mini"


def test_criteria_provenance_is_not_shown_to_role_a(settings, catalog):
    """§0 is an exact list; the generator must not learn who grades it."""
    seen: dict = {}

    def spy(i, scratch):
        seen["inputs"] = i
        return stub_harness(i, scratch)

    dispatch(_inputs(settings), catalog, spy, criteria_authored_by="gpt-4o-mini")
    assert not hasattr(seen["inputs"], "criteria_authored_by")


# --- B2b entry point (criteria authored, never supplied) --------------------

def test_dispatch_b2b_authors_the_criteria_it_dispatches_against(settings, catalog):
    from orchestrator.criteria import Criteria

    authored = Criteria("BC-1: returns only ERROR records.\n"
                        "BC-2: reports failure when the log source is unreachable.",
                        "gpt-4o-mini")
    result = dispatch_b2b(TASK, catalog, LOCK, settings, HARNESS, stub_harness,
                          catalog_ref="a" * 40,
                          author=lambda t, r=None: authored)
    assert result.outcome == "candidate"
    data = yaml.safe_load(
        Path(result.candidate_dir).with_suffix(".inputs.yaml").read_text(encoding="utf-8"))
    assert data["behavioral_criteria"] == authored.text
    assert data["criteria_authored_by"] == "gpt-4o-mini"


def test_dispatch_b2b_defaults_to_the_stub_author_without_credentials(
        settings, catalog, monkeypatch):
    monkeypatch.delenv("ORCH_CRITERIA", raising=False)
    result = dispatch_b2b(TASK, catalog, LOCK, settings, HARNESS, stub_harness,
                          catalog_ref="a" * 40)
    data = yaml.safe_load(
        Path(result.candidate_dir).with_suffix(".inputs.yaml").read_text(encoding="utf-8"))
    assert data["criteria_authored_by"] == "stub"
    assert data["behavioral_criteria"].startswith("BC-1: ")


def test_dispatch_b2b_does_not_dispatch_when_criteria_cannot_be_authored(
        settings, catalog):
    from orchestrator.criteria import CriteriaError

    def broken(task_spec, residual=None):
        raise CriteriaError("model unreachable")

    with pytest.raises(CriteriaError):
        dispatch_b2b(TASK, catalog, LOCK, settings, HARNESS, stub_harness,
                     catalog_ref="a" * 40, author=broken)
    proposed = settings.catalog_root / PROPOSED_DIR
    assert not proposed.exists() or not list(proposed.iterdir())


def test_dispatch_b2b_refuses_a_same_family_author(settings, catalog):
    from orchestrator.criteria import Criteria, CriteriaError

    with pytest.raises(CriteriaError, match="not independent"):
        dispatch_b2b(TASK, catalog, LOCK, settings, HARNESS, stub_harness,
                     catalog_ref="a" * 40,
                     author=lambda t, r=None: Criteria("BC-1: x", "claude-haiku-4-5"),
                     impl_profile={"provider": "anthropic",
                                   "profile_class": "claude-class"})


def test_refused_dispatch_persists_no_inputs(settings, catalog):
    result = dispatch(_inputs(settings, scope="delta"), catalog, stub_harness)
    assert result.outcome == "refused"
    proposed = settings.catalog_root / PROPOSED_DIR
    assert not list(proposed.glob("*.inputs.yaml")) if proposed.is_dir() else True


def test_generation_contract_states_what_certify_mechanically_requires():
    """Layer 6/7: certify refuses a candidate without `eval/run.py` or a
    declared `slot_value_surface`, and neither is named in the playbook §6
    layout. A real harness cannot infer them, so the contract every Role A
    backend is given must state them — otherwise every real generation fails
    certify for a reason the generator was never told about."""
    from certify.evalrun import PROFILE_ENV, REPORT_ENV, RUNNER_REL
    from orchestrator.dispatch import _AGENT_CLI_SYSTEM, _AGENT_SYSTEM

    for prompt in (_AGENT_SYSTEM, _AGENT_CLI_SYSTEM):
        assert RUNNER_REL.as_posix() in prompt
        assert REPORT_ENV in prompt
        assert PROFILE_ENV in prompt
        assert "slot_value_surface" in prompt


def test_cli_cap_is_the_generation_ceiling_not_the_iterate_budget():
    """Two different quantities, and capping the process with the wrong one
    truncates every run. §5.7's 25 budgets step 7 alone — the iterate-to-green
    loop. A CLI's turn counter covers all eight steps, ~18 file writes
    included, so the process cap has to be the generation ceiling. Measured:
    a correct candidate costs 26–28 CLI turns, which the iterate budget
    forbids before the harness has any say in it."""
    from certify.conformance import GENERATION_CEILING, TURN_BUDGET
    from orchestrator.dispatch import MAX_TURNS

    assert MAX_TURNS == GENERATION_CEILING
    assert GENERATION_CEILING > TURN_BUDGET


def test_manifest_examples_in_the_contract_are_block_yaml():
    """The slot_value_surface example was flow-style ({name: …, location: …}),
    and a location like `bindings[0]` makes that line unparseable YAML — the
    harness then writes a manifest certify cannot load. Block style has no
    such trap."""
    from orchestrator.dispatch import _AGENT_CLI_SYSTEM, _AGENT_SYSTEM

    for prompt in (_AGENT_SYSTEM, _AGENT_CLI_SYSTEM):
        slot_line = next(ln for ln in prompt.splitlines() if "slot_value_surface" in ln)
        assert "{name" not in slot_line, slot_line


def _inputs_for_cli() -> HarnessInputs:
    return HarnessInputs(
        task_spec="List error entries.",
        behavioral_criteria="BC-1: returns only ERROR records.",
        catalog_ref="4c0a1b7e" * 5,
        capability_manifest={"entries": []},
        trust_tier_ceiling=TRUST_TIER_CEILING,
        mode="B2b",
        scope="full-agent",
        model_profile="stub-class-ref",
        harness="claude-code/2.1.228",
        target_runtime="langgraph-py311",
    )


def _fake_cli(monkeypatch, *, returncode: int, manifest_status: str) -> None:
    """Stand in for `claude -p`: writes a manifest, then exits `returncode`."""
    import subprocess as sp

    from certify.playbook import MANIFEST_NAME

    def fake_run(argv, **kw):
        (Path(kw["cwd"]) / MANIFEST_NAME).write_text(
            yaml.safe_dump({"agent_id": "a", "kind": "agent", "mode": "B2b",
                            "scope": "full-agent", "status": manifest_status}),
            encoding="utf-8")
        return sp.CompletedProcess(argv, returncode, stdout='{"num_turns": 9}',
                                   stderr="")

    monkeypatch.setattr(sp, "run", fake_run)


def test_cli_exit_code_never_discards_the_artifacts(tmp_path, monkeypatch):
    """CATALOG §10: artifacts are the evidence, a harness's own narration is
    not. `claude -p` exits nonzero when it exhausts its turn budget, with a
    full candidate already on disk — reporting that as ERROR throws the
    evidence away. §5.7 caps a truncated run at PARTIAL, not COMPLETE."""
    from orchestrator.dispatch import claude_cli_harness

    _fake_cli(monkeypatch, returncode=1, manifest_status="COMPLETE")
    out = tmp_path / "truncated"
    out.mkdir()
    outcome = claude_cli_harness(_inputs_for_cli(), out)

    assert outcome.status == "PARTIAL", "a truncated run cannot claim COMPLETE"
    assert any("exit 1" in c for c in outcome.codes), outcome.codes


def test_cli_clean_exit_keeps_the_manifest_status(tmp_path, monkeypatch):
    """The PARTIAL ceiling is for truncated runs only — a clean exit must
    still be able to report COMPLETE."""
    from orchestrator.dispatch import claude_cli_harness

    _fake_cli(monkeypatch, returncode=0, manifest_status="COMPLETE")
    out = tmp_path / "clean"
    out.mkdir()
    assert claude_cli_harness(_inputs_for_cli(), out).status == "COMPLETE"
