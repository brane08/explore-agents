"""Certify step 3 — the candidate's own eval suite (CHECKLISTS layer 7).

The [M] item: "`eval/` green on the manifest's `model_profile`, evidence
captured." Both halves are load-bearing. Green-without-evidence is not
certifiable: the whole point of `eval/` is that a model migration reruns it, and
a rerun you cannot compare against a recorded baseline proves nothing.

The adversarial cases here mirror the layer-12 harness doubles: a runner that
exits 0 while its own report names a failed criterion is the exit-code twin of a
harness echoing an all-true checklist over violating artifacts.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from certify.candidate import load_candidate
from certify.evalrun import (
    EVIDENCE_NAME,
    EvalOutcome,
    load_eval_evidence,
    subprocess_runner,
)
from certify.playbook import HarnessInputs
from certify.steps import StepResult, step_eval
from certify_fixtures import LOCK, write_candidate

PROFILE = "stub-class-ref"


def _inputs(**over) -> HarnessInputs:
    base = dict(
        task_spec="List the error-level log entries.",
        behavioral_criteria="BC-1: only error-level records are returned.",
        catalog_ref="0" * 40,
        capability_manifest=LOCK,
        trust_tier_ceiling="validated",
        mode="B2b",
        scope="full-agent",
        model_profile=PROFILE,
        harness="stub-harness",
        target_runtime="langgraph",
    )
    base.update(over)
    return HarnessInputs(**base)


def _runner_src(criteria: list[tuple[str, bool]], *,
                exit_code: str = "0 if ok else 1",
                profile: str = "os.environ.get('MODEL_PROFILE', '')",
                write_report: bool = True) -> str:
    """A runner honouring the §5 contract, parameterized so each test can break
    exactly one clause of it."""
    return (
        "import json, os, sys\n"
        f"criteria = {[{'id': cid, 'passed': ok} for cid, ok in criteria]!r}\n"
        "ok = all(c['passed'] for c in criteria)\n"
        f"report = {{'model_profile': {profile}, 'criteria': criteria}}\n"
        + ("path = os.environ.get('EVAL_REPORT')\n"
           "if path:\n"
           "    open(path, 'w', encoding='utf-8').write(json.dumps(report))\n"
           "else:\n"
           "    sys.stdout.write(json.dumps(report))\n" if write_report else "")
        + f"sys.exit({exit_code})\n"
    )


@pytest.fixture
def candidate_dir(tmp_path: Path) -> Path:
    return tmp_path / "_proposed" / "log-error-lister"


def _candidate(candidate_dir: Path, **over):
    write_candidate(candidate_dir, _inputs(), **over)
    return load_candidate(candidate_dir)


# --- the happy path is the only one that may pass ----------------------------

def test_eval_runs_the_candidates_runner_and_passes_when_green(candidate_dir):
    result = step_eval(_candidate(candidate_dir))
    assert result.passed, result.detail
    assert result.evidence["passed"] is True
    assert result.evidence["exit_code"] == 0


def test_per_criterion_results_are_captured_not_just_the_exit_code(candidate_dir):
    candidate = _candidate(candidate_dir, eval_body=_runner_src(
        [("BC-1", True), ("BC-2", True), ("IC-1", True)]))
    result = step_eval(candidate)
    assert result.passed, result.detail
    assert [c["id"] for c in result.evidence["criteria"]] == ["BC-1", "BC-2", "IC-1"]


# --- red is red ---------------------------------------------------------------

def test_eval_fails_when_a_criterion_fails(candidate_dir):
    candidate = _candidate(candidate_dir, eval_body=_runner_src(
        [("BC-1", True), ("BC-2", False)]))
    result = step_eval(candidate)
    assert not result.passed
    assert "BC-2" in result.detail


def test_eval_fails_when_the_runner_crashes(candidate_dir):
    candidate = _candidate(candidate_dir,
                           eval_body="raise RuntimeError('boom')\n")
    result = step_eval(candidate)
    assert not result.passed
    assert "boom" in result.detail


def test_exit_zero_cannot_override_a_failing_criterion_in_the_report(candidate_dir):
    """The exit code is a claim; the report is the evidence for it. A runner
    that disagrees with itself is refused, not resolved in its own favour."""
    candidate = _candidate(candidate_dir, eval_body=_runner_src(
        [("BC-1", False)], exit_code="0"))
    result = step_eval(candidate)
    assert not result.passed
    assert "exit 0" in result.detail and "BC-1" in result.detail


# --- evidence is half the [M] item -------------------------------------------

def test_green_without_a_report_is_not_certifiable(candidate_dir):
    """Exit 0 and nothing else: nothing to rerun against after a migration."""
    candidate = _candidate(candidate_dir, eval_body="import sys\nsys.exit(0)\n")
    result = step_eval(candidate)
    assert not result.passed
    assert "no per-criterion report" in result.detail


def test_report_without_criteria_is_not_evidence(candidate_dir):
    candidate = _candidate(candidate_dir, eval_body=_runner_src([]))
    result = step_eval(candidate)
    assert not result.passed
    assert "no criteria" in result.detail


def test_eval_fails_when_the_runner_is_missing(candidate_dir):
    candidate = _candidate(candidate_dir)
    (candidate_dir / "eval" / "run.py").unlink()
    result = step_eval(candidate)
    assert not result.passed
    assert "eval/run.py" in result.detail


def test_evidence_is_persisted_for_the_promotion_commit(candidate_dir):
    step_eval(_candidate(candidate_dir))
    stored = json.loads((candidate_dir / "trace" / EVIDENCE_NAME)
                        .read_text(encoding="utf-8"))
    assert stored["passed"] is True
    assert stored["model_profile"] == PROFILE


def test_a_red_run_is_persisted_too(candidate_dir):
    """Promotion must be able to see that eval ran *and failed* — an absent file
    would be indistinguishable from never having run."""
    candidate = _candidate(candidate_dir,
                           eval_body=_runner_src([("BC-1", False)]))
    step_eval(candidate)
    assert load_eval_evidence(candidate_dir)["passed"] is False


def test_evidence_is_persisted_for_a_b2a_candidate_with_no_trace_dir(candidate_dir):
    candidate = _candidate(candidate_dir, trace=False)
    assert step_eval(candidate).passed
    assert load_eval_evidence(candidate_dir) is not None


def test_evidence_is_byte_identical_across_runs(candidate_dir):
    """It lands in the promotion commit, so it obeys the determinism rule the
    rest of the tooling does — no timestamps, no set-ordering leaks."""
    candidate = _candidate(candidate_dir)
    step_eval(candidate)
    first = (candidate_dir / "trace" / EVIDENCE_NAME).read_bytes()
    step_eval(candidate)
    assert (candidate_dir / "trace" / EVIDENCE_NAME).read_bytes() == first


# --- the profile the evidence is valid for ------------------------------------

def test_the_runner_is_told_which_profile_it_runs_under(candidate_dir):
    candidate = _candidate(candidate_dir, eval_body=_runner_src([("BC-1", True)]))
    result = step_eval(candidate)
    assert result.evidence["model_profile"] == PROFILE


def test_eval_fails_when_the_report_claims_a_different_profile(candidate_dir):
    """Evidence is only valid for one profile (layer 8: class migration = rerun,
    never inherited evidence), so a report that names another one is refused."""
    candidate = _candidate(candidate_dir, eval_body=_runner_src(
        [("BC-1", True)], profile="'some-other-profile'"))
    result = step_eval(candidate)
    assert not result.passed
    assert "some-other-profile" in result.detail


def test_eval_fails_when_the_manifest_declares_no_profile(candidate_dir):
    candidate = _candidate(candidate_dir)
    manifest = candidate_dir / "AGENT_MANIFEST.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            f"model_profile: {PROFILE}", "model_profile: ''"),
        encoding="utf-8")
    result = step_eval(load_candidate(candidate_dir))
    assert not result.passed
    assert "model_profile" in result.detail


# --- running untrusted code ----------------------------------------------------

def test_eval_refuses_to_run_behind_a_failed_security_scan(candidate_dir):
    """Step 3 executes candidate-authored code, so the static scan gates it. The
    rule lives here rather than at each call site: a caller that forgets the
    ordering would execute code certify already flagged."""
    calls = []

    def spy(path, profile):
        calls.append(path)
        return EvalOutcome(exit_code=0, report={"criteria": []})

    result = step_eval(_candidate(candidate_dir), runner=spy,
                       security=StepResult("security_static", False, "secret in src/"))
    assert not result.passed
    assert calls == []
    assert "security" in result.detail


def test_eval_times_out_instead_of_hanging(candidate_dir, monkeypatch):
    monkeypatch.setenv("CERTIFY_EVAL_TIMEOUT", "1")
    candidate = _candidate(candidate_dir,
                           eval_body="import time\ntime.sleep(30)\n")
    result = step_eval(candidate)
    assert not result.passed
    assert "timed out" in result.detail


def test_the_runner_is_a_seam(candidate_dir):
    """Same seam discipline as the harness and scorer: certify never hard-codes
    how a candidate's eval is executed."""
    result = step_eval(_candidate(candidate_dir), runner=lambda p, prof: EvalOutcome(
        exit_code=0, report={"model_profile": prof,
                             "criteria": [{"id": "BC-1", "passed": True}]}))
    assert result.passed, result.detail


def test_subprocess_runner_reports_a_missing_runner_rather_than_raising(candidate_dir):
    _candidate(candidate_dir)
    (candidate_dir / "eval" / "run.py").unlink()
    outcome = subprocess_runner(candidate_dir, PROFILE)
    assert outcome.report is None and outcome.exit_code is None
