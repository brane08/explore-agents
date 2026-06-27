"""
evaluator.py — Step 5 of the B2a loop.

Judges a SandboxResult against the fixed eval suite.

The generated agent is expected to print a JSON object to stdout keyed by
eval case id, e.g.:
  {"search_error_level": {...}, "aggregate_by_service": {...}}

EvalResult.passed is True only when every selected case passes.
EvalResult.score is the fraction of cases that passed (0.0–1.0).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from eval_suite.log_analysis_cases import CASE_INDEX
from loop.sandbox_runner import SandboxResult
from loop.spec import EvalCaseSpec


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class EvalResult:
    passed: bool
    score: float                        # fraction of cases that passed
    case_results: tuple[CaseResult, ...]
    stop_reason: str = ""               # populated on early exit (timeout, crash, bad json)


def evaluate(
    sandbox: SandboxResult,
    eval_cases: list[EvalCaseSpec],
) -> EvalResult:
    """
    Run the check functions from the eval suite against the sandbox stdout.

    Returns EvalResult regardless of outcome — never raises.
    """
    if not eval_cases:
        return EvalResult(passed=False, score=0.0, case_results=(),
                          stop_reason="no eval cases provided")

    # --- hard stops ---
    if sandbox.timed_out:
        cr = tuple(CaseResult(c.id, False, "sandbox timed out") for c in eval_cases)
        return EvalResult(passed=False, score=0.0, case_results=cr, stop_reason="timeout")

    if sandbox.exit_code != 0:
        detail = (sandbox.stderr or "").strip()[:200]
        cr = tuple(CaseResult(c.id, False, f"sandbox exited {sandbox.exit_code}: {detail}")
                   for c in eval_cases)
        return EvalResult(passed=False, score=0.0, case_results=cr,
                          stop_reason="crash")

    # --- parse stdout ---
    try:
        outputs: dict = json.loads(sandbox.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        cr = tuple(CaseResult(c.id, False, f"stdout not valid JSON: {exc}") for c in eval_cases)
        return EvalResult(passed=False, score=0.0, case_results=cr, stop_reason="bad json")

    # --- run checks ---
    results: list[CaseResult] = []
    for case_spec in eval_cases:
        if case_spec.id not in CASE_INDEX:
            results.append(CaseResult(case_spec.id, False, f"unknown case id {case_spec.id!r}"))
            continue

        if case_spec.id not in outputs:
            results.append(CaseResult(case_spec.id, False, "case id missing from agent output"))
            continue

        tool_result = outputs[case_spec.id]
        eval_case = CASE_INDEX[case_spec.id]
        try:
            passed = bool(eval_case.check(tool_result))
            detail = "" if passed else f"check failed on: {json.dumps(tool_result)[:120]}"
        except Exception as exc:
            passed = False
            detail = f"check raised {type(exc).__name__}: {exc}"

        results.append(CaseResult(case_spec.id, passed, detail))

    n_passed = sum(1 for r in results if r.passed)
    score = n_passed / len(results)
    passed = n_passed == len(results)
    return EvalResult(passed=passed, score=score, case_results=tuple(results))
