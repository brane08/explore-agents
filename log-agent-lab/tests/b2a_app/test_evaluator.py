"""
Tests for evaluator.py.

Key requirement from the playbook: verify the evaluator correctly fails
a deliberately broken candidate before trusting it to pass a real one.
"""

import json
from loop.evaluator import CaseResult, EvalResult, evaluate
from loop.sandbox_runner import SandboxResult
from loop.spec import EvalCaseSpec

# --- helpers ---

def _ok(output: dict) -> SandboxResult:
    return SandboxResult(exit_code=0, stdout=json.dumps(output), stderr="", timed_out=False)

def _crash(stderr: str = "boom") -> SandboxResult:
    return SandboxResult(exit_code=1, stdout="", stderr=stderr, timed_out=False)

def _timeout() -> SandboxResult:
    return SandboxResult(exit_code=None, stdout="", stderr="", timed_out=True)

def _bad_json() -> SandboxResult:
    return SandboxResult(exit_code=0, stdout="not json", stderr="", timed_out=False)


_CASES = [
    EvalCaseSpec(id="search_error_level", tool_name="es_search",
                 arguments={"query_level": "ERROR", "size": 10}),
    EvalCaseSpec(id="field_stats_numeric", tool_name="field_stats",
                 arguments={"field": "duration_ms"}),
]

_GOOD_OUTPUT = {
    "search_error_level": {"hits": [{"level": "ERROR"}], "total": 1},
    "field_stats_numeric": {"min": 1.0, "max": 500.0, "avg": 250.0, "count": 100},
}

_BAD_OUTPUT = {
    "search_error_level": {"hits": [{"level": "INFO"}], "total": 1},  # wrong level
    "field_stats_numeric": {"cardinality": 4},                          # missing keys
}


# --- timeout / crash / bad json ---

def test_timeout_fails_all_cases():
    result = evaluate(_timeout(), _CASES)
    assert not result.passed
    assert result.score == 0.0
    assert result.stop_reason == "timeout"
    assert all(not r.passed for r in result.case_results)


def test_crash_fails_all_cases():
    result = evaluate(_crash("segfault"), _CASES)
    assert not result.passed
    assert result.score == 0.0
    assert "crash" in result.stop_reason
    assert all(not r.passed for r in result.case_results)


def test_bad_json_fails_all_cases():
    result = evaluate(_bad_json(), _CASES)
    assert not result.passed
    assert result.score == 0.0
    assert result.stop_reason == "bad json"


def test_crash_stderr_included_in_detail():
    result = evaluate(_crash("ImportError: no module"), _CASES)
    assert any("ImportError" in r.detail for r in result.case_results)


# --- broken candidate correctly fails ---

def test_broken_output_fails_checks():
    """Core requirement: evaluator must fail bad output before we trust it on good output."""
    result = evaluate(_ok(_BAD_OUTPUT), _CASES)
    assert not result.passed
    assert result.score == 0.0
    assert all(not r.passed for r in result.case_results)


def test_missing_case_id_in_output_fails_that_case():
    partial = {"search_error_level": {"hits": [{"level": "ERROR"}], "total": 1}}
    result = evaluate(_ok(partial), _CASES)
    failed = [r for r in result.case_results if not r.passed]
    assert any(r.case_id == "field_stats_numeric" for r in failed)
    assert "missing" in failed[0].detail or "missing" in failed[-1].detail


# --- good candidate passes ---

def test_good_output_passes_all_cases():
    result = evaluate(_ok(_GOOD_OUTPUT), _CASES)
    assert result.passed
    assert result.score == 1.0
    assert all(r.passed for r in result.case_results)


# --- partial pass ---

def test_partial_pass_score():
    mixed = {
        "search_error_level": {"hits": [{"level": "ERROR"}], "total": 1},  # passes
        "field_stats_numeric": {"cardinality": 4},                           # fails
    }
    result = evaluate(_ok(mixed), _CASES)
    assert not result.passed
    assert result.score == 0.5


# --- structural ---

def test_no_eval_cases_fails():
    result = evaluate(_ok({}), [])
    assert not result.passed
    assert result.stop_reason == "no eval cases provided"


def test_eval_result_fields():
    result = evaluate(_ok(_GOOD_OUTPUT), _CASES)
    assert isinstance(result, EvalResult)
    assert isinstance(result.case_results, tuple)
    assert len(result.case_results) == 2


def test_case_result_detail_empty_on_pass():
    result = evaluate(_ok(_GOOD_OUTPUT), _CASES)
    assert all(r.detail == "" for r in result.case_results if r.passed)
