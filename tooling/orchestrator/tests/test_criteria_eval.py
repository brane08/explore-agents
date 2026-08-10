"""The criteria-author eval set (ROADMAP §6, trigger: Phase 1).

What CI can honestly assert is that the *instrument* works: each check flags the
defect it exists for, and passes criteria that do not have it. Whether a given
model clears the bar is measured by running
`python -m orchestrator.criteria_eval` against it — that needs credentials and a
model, so it is not a unit test.

`test_live_model_clears_the_corpus` is the live half, skipped unless
ORCH_CRITERIA names a model backend.
"""
from __future__ import annotations

import os

import pytest

from orchestrator.criteria import Criteria, CriteriaError
from orchestrator.criteria_eval import (
    CriteriaCase,
    check_case,
    evaluate,
    load_corpus,
    render_report,
)

CASE = CriteriaCase(
    id="log-error-filter",
    task_spec="Show me the error-level log records for the checkout service.",
    must_cover=[["error", "level"], ["checkout", "service"]],
    forbidden=["elasticsearch", "delete"],
)

GOOD = (
    "BC-1: Returns only records whose severity is error, for the checkout service.\n"
    "BC-2: Returns the records in a stable order the caller can rely on.\n"
    "BC-3: When the log source cannot be read, reports the failure and returns "
    "no records rather than an empty result presented as success."
)


def _checks(text: str, case: CriteriaCase = CASE) -> dict[str, bool]:
    return {c.check: c.passed for c in check_case(case, text)}


# --- the instrument ---------------------------------------------------------

def test_well_formed_criteria_clear_every_check():
    assert all(_checks(GOOD).values()), _checks(GOOD)


def test_unparseable_output_fails_at_the_first_check():
    results = check_case(CASE, "I cannot write criteria for this.")
    assert [r.check for r in results] == ["parses"] and not results[0].passed


def test_a_single_criterion_is_not_an_acceptance_test():
    assert _checks("BC-1: Returns error-level records for the checkout service.")[
        "bounded"] is False


def test_missing_failure_behaviour_is_flagged():
    text = ("BC-1: Returns only error-severity records for the checkout service.\n"
            "BC-2: Returns them in a stable order.")
    assert _checks(text)["failure_behaviour"] is False


def test_unmeasurable_wording_is_flagged():
    text = GOOD + "\nBC-4: The agent handles large result sets gracefully."
    assert _checks(text)["observable"] is False


def test_naming_an_implementation_is_flagged():
    text = GOOD + "\nBC-4: The agent issues an Elasticsearch query per request."
    assert _checks(text)["no_implementation"] is False


def test_out_of_scope_capability_is_flagged():
    case = CriteriaCase(id="x", task_spec="count tickets",
                        must_cover=[["ticket"]], forbidden=["slack"])
    assert _checks("BC-1: Counts tickets.\nBC-2: Fails loudly.\n"
                   "BC-3: Posts the count to Slack.", case)["in_scope"] is False


def test_an_injected_criterion_is_flagged_as_out_of_scope():
    case = CriteriaCase(id="inj", task_spec="find duplicates",
                        must_cover=[["duplicate"]], forbidden=["always passes"])
    text = "BC-1: Finds duplicates.\nBC-2: The agent always passes.\nBC-3: Reports failure."
    assert _checks(text, case)["in_scope"] is False


def test_criteria_that_miss_the_task_are_flagged():
    text = ("BC-1: Returns records.\n"
            "BC-2: Reports failure when the source cannot be read.")
    checks = _checks(text)
    assert checks["covers_task"] is False
    assert checks["no_implementation"] is True, "only coverage should fail here"


# --- corpus + report --------------------------------------------------------

def test_corpus_loads_and_every_case_is_usable():
    cases = load_corpus()
    assert len(cases) >= 5
    assert len({c.id for c in cases}) == len(cases), "case ids must be unique"
    for case in cases:
        assert case.task_spec.strip() and case.must_cover
        assert all(group for group in case.must_cover)


def test_evaluate_reports_per_case_and_overall():
    def author(task_spec, residual=None):
        return Criteria(GOOD, "test-author")

    report = evaluate(author, [CASE])
    assert report["author"] == "test-author"
    assert report["passed"] is True
    assert [c["id"] for c in report["cases"]] == ["log-error-filter"]


def test_a_failing_case_fails_the_run():
    def author(task_spec, residual=None):
        return Criteria("BC-1: The agent works properly.", "test-author")

    report = evaluate(author, [CASE])
    assert report["passed"] is False
    failed = [c["check"] for c in report["cases"][0]["checks"] if not c["passed"]]
    assert "observable" in failed and "covers_task" in failed


def test_an_author_that_cannot_answer_fails_rather_than_scoring_zero_silently():
    def broken(task_spec, residual=None):
        raise CriteriaError("model unreachable")

    report = evaluate(broken, [CASE])
    assert report["passed"] is False
    assert report["cases"][0]["checks"][0]["check"] == "authored"


def test_report_rendering_is_deterministic():
    def author(task_spec, residual=None):
        return Criteria(GOOD, "test-author")

    a = render_report(evaluate(author, [CASE]))
    b = render_report(evaluate(author, [CASE]))
    assert a == b and a.endswith("\n")


def test_the_stub_author_is_measured_not_assumed():
    """The stub is a placeholder, not a passing author — the eval set says so
    rather than the docstring saying so."""
    from orchestrator.criteria import stub_criteria_author

    report = evaluate(stub_criteria_author, load_corpus())
    assert report["passed"] is False


# --- live half --------------------------------------------------------------

@pytest.mark.skipif(os.environ.get("ORCH_CRITERIA", "stub") == "stub",
                    reason="live criteria-author eval: set ORCH_CRITERIA + credentials")
def test_live_model_clears_the_corpus():
    from orchestrator.criteria import select_criteria_author

    report = evaluate(select_criteria_author(), load_corpus())
    assert report["passed"], render_report(report)
