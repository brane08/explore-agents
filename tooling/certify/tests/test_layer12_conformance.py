"""CHECKLISTS layer 12 [M] — harness conformance, per harness/version.

Two duties, both tested: the suite must PASS a harness that follows the
playbook, and FAIL each way a harness can cheat. The second half is what makes
the conformance list mean anything (CATALOG §10).
"""
from __future__ import annotations

import pytest

from certify.conformance import (
    FIXTURES,
    fixture_budget_and_trace,
    fixture_criteria_bait,
    fixture_delta,
    fixture_delta_insufficient,
    fixture_layout,
    fixture_missing_input,
    fixture_unsatisfiable,
    run_conformance,
)
from conftest import (
    agent_binder_harness,
    budget_burner_harness,
    conformant_harness,
    criteria_editor_harness,
    false_echo_harness,
    improviser_harness,
    liar_harness,
    model_hardcoder_harness,
    scope_widener_harness,
    skipper_harness,
    tool_inventor_harness,
    ui_shipping_harness,
)


def test_conformant_harness_passes_every_fixture(lock, tmp_path):
    report = run_conformance(conformant_harness, "fixture-harness/0", lock, tmp_path)
    failed = [(r.fixture_id, r.detail) for r in report.results if not r.passed]
    assert failed == [], failed
    assert report.conformant
    assert len(report.results) == len(FIXTURES)


def test_report_echo_lists_every_fixture(lock, tmp_path):
    report = run_conformance(conformant_harness, "fixture-harness/0", lock, tmp_path)
    echo = report.echo()
    assert "conformant: true" in echo
    for r in report.results:
        assert r.fixture_id in echo


# --- each cheat must be caught by the fixture that owns it -------------------

def test_false_complete_on_unsatisfiable_is_caught(lock, tmp_path):
    result = fixture_unsatisfiable(liar_harness, lock, tmp_path)
    assert not result.passed
    assert "false COMPLETE" in result.detail


def test_criteria_modification_is_caught_by_hash(lock, tmp_path):
    """The harness even emits the right WARN — only the hash exposes the edit."""
    result = fixture_criteria_bait(criteria_editor_harness, lock, tmp_path)
    assert not result.passed
    assert "criteria were modified" in result.detail


def test_agent_binding_is_caught(lock, tmp_path):
    result = fixture_layout(agent_binder_harness, lock, tmp_path)
    assert not result.passed
    assert "shipped-agent" in result.detail


def test_shipped_ui_is_caught(lock, tmp_path):
    result = fixture_layout(ui_shipping_harness, lock, tmp_path)
    assert not result.passed
    assert "UI asset" in result.detail


def test_hardcoded_model_id_is_caught(lock, tmp_path):
    result = fixture_layout(model_hardcoder_harness, lock, tmp_path)
    assert not result.passed
    assert "hardcoded model id" in result.detail


def test_skipped_criterion_test_is_caught(lock, tmp_path):
    result = fixture_layout(skipper_harness, lock, tmp_path)
    assert not result.passed
    assert "skips a test" in result.detail


def test_invented_tool_is_caught(lock, tmp_path):
    result = fixture_layout(tool_inventor_harness, lock, tmp_path)
    assert not result.passed
    assert "invented tool" in result.detail


def test_binding_above_ceiling_is_caught(lock, tmp_path):
    result = fixture_layout(false_echo_harness, lock, tmp_path)
    assert not result.passed
    assert "TRUST_TIER_CEILING" in result.detail


def test_false_checklist_echo_is_caught(lock, tmp_path):
    """The harness claims all-true; the artifacts say otherwise."""
    result = fixture_budget_and_trace(false_echo_harness, lock, tmp_path)
    assert not result.passed
    assert "disagrees with the artifacts" in result.detail


def test_budget_overrun_is_caught(lock, tmp_path):
    result = fixture_budget_and_trace(budget_burner_harness, lock, tmp_path)
    assert not result.passed
    assert "turn budget exceeded" in result.detail


def test_silent_scope_widening_is_caught(lock, tmp_path):
    result = fixture_delta_insufficient(scope_widener_harness, lock, tmp_path)
    assert not result.passed
    assert "DELTA_INSUFFICIENT" in result.detail


def test_improvising_around_missing_input_is_caught(lock, tmp_path):
    result = fixture_missing_input(improviser_harness, lock, tmp_path)
    assert not result.passed
    assert "MISSING_INPUT" in result.detail


def test_rebuilding_template_capability_is_caught(lock, tmp_path):
    """A delta that binds the template's own source instead of filling the gap."""
    from conftest import write_candidate
    from certify.conformance import HarnessOutcome

    def rebuilder(inputs, out):
        write_candidate(out, inputs, kind="component", bindings=["es_search"],
                        target_slot={"template_id": "report-template", "slot": "source"})
        return HarnessOutcome("COMPLETE", [], turns_used=9)

    result = fixture_delta(rebuilder, lock, tmp_path)
    assert not result.passed
    assert "rebuilt the template-provided" in result.detail


def test_crashing_harness_is_non_conformant(lock, tmp_path):
    def crasher(inputs, out):
        raise RuntimeError("harness exploded")

    report = run_conformance(crasher, "crasher/0", lock, tmp_path)
    assert not report.conformant
    assert all("harness exploded" in r.detail for r in report.results)


@pytest.mark.parametrize("harness", [
    liar_harness, criteria_editor_harness, agent_binder_harness,
    ui_shipping_harness, model_hardcoder_harness, skipper_harness,
    tool_inventor_harness, false_echo_harness, scope_widener_harness,
    budget_burner_harness, improviser_harness,
], ids=lambda h: h.__name__)
def test_no_cheating_harness_reaches_the_conformance_list(harness, lock, tmp_path):
    report = run_conformance(harness, f"{harness.__name__}/0", lock, tmp_path)
    assert not report.conformant, "a cheating harness must never be listed"
