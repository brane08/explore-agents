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
from certify.conformance import HarnessOutcome

from orchestrator.config import Settings
from orchestrator.dispatch import (
    PROPOSED_DIR,
    TRUST_TIER_CEILING,
    build_inputs,
    candidate_id,
    dispatch,
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


def test_refused_dispatch_persists_no_inputs(settings, catalog):
    result = dispatch(_inputs(settings, scope="delta"), catalog, stub_harness)
    assert result.outcome == "refused"
    proposed = settings.catalog_root / PROPOSED_DIR
    assert not list(proposed.glob("*.inputs.yaml")) if proposed.is_dir() else True
