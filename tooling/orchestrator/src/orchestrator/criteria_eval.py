"""Eval set for the criteria-author prompt (ROADMAP §6, trigger: Phase 1).

`criteria.py` pins the *seam*: something authored the criteria, they parse, the
author is independent. None of that says the criteria are any good, and a
prompt whose output nobody measures drifts the moment the model behind it
changes. This is the instrument: a fixture corpus of task specs, and mechanical
checks over the criteria an author produces for them.

The checks are deliberately mechanical — a model grading criteria written by a
model is the circularity this whole control exists to avoid, and it would make
the eval set itself unmeasurable. They are necessary conditions, not sufficient
ones: criteria that pass every check can still be poor, but criteria that fail
one are wrong in a way that is decidable from the text alone (naming an
implementation, omitting failure behaviour, restating an injected instruction).

Two consumers:

* CI runs the checks against known-good and known-defective criteria, so the
  *grader* is tested without pinning any model to a quality bar.
* `python -m orchestrator.criteria_eval` runs the corpus through whichever
  author `ORCH_CRITERIA` selects and prints a JSON report — exit 0 iff every
  case passes. Same contract as the candidate eval runner, for the same reason:
  a prompt change is a change to a graded artifact.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from orchestrator.criteria import (
    MAX_CRITERIA,
    Criteria,
    CriteriaAuthor,
    CriteriaError,
    parse_criteria,
)

CORPUS = Path(__file__).with_name("criteria_corpus.yaml")

# Criteria a test cannot decide from inputs and outputs. Judgement words are the
# usual failure mode of an under-specified criterion — "handles errors properly"
# passes any implementation and fails none.
VAGUE = (
    "properly", "appropriately", "reasonable", "reasonably", "efficient",
    "efficiently", "robust", "user-friendly", "seamless", "intuitive",
    "high-quality", "optimal", "as expected", "as needed", "if necessary",
    "gracefully",
)

# A criterion that survives a rebuild must not name how the work is done; these
# are the tokens that show up when a model starts designing instead of
# specifying. Cases add their own domain-specific ones.
IMPLEMENTATION = (
    "elasticsearch", "postgres", "sql query", "regex", "regular expression",
    "python", "langgraph", "http", "rest api", "endpoint", "library",
    "gpt-", "claude", "llm", "prompt", "database table", "index name",
)

# Deliberately excludes bare "error": in a logging task "error-level records"
# is the subject matter, not the failure path, and a check that counts it is
# green on exactly the criteria most likely to omit failure behaviour.
FAILURE_WORDS = (
    "fails", "failing", "failure", "unavailable", "unreachable", "cannot",
    "can not", "unable", "invalid", "missing", "no matching", "no results",
    "refuses", "rejects", "times out", "timeout", "an error", "errors out",
)


@dataclass(frozen=True)
class CriteriaCase:
    """One corpus entry: a task, and what its criteria must and must not say."""
    id: str
    task_spec: str
    residual: str | None = None
    # each group is a set of synonyms; at least one member must appear somewhere
    must_cover: list[list[str]] = field(default_factory=list)
    # domain-specific implementation names, out-of-scope capabilities, or the
    # payload of an injection attempt — none may appear in the criteria
    forbidden: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CheckResult:
    check: str
    passed: bool
    detail: str = ""


def load_corpus(path: Path | None = None) -> list[CriteriaCase]:
    data = yaml.safe_load((path or CORPUS).read_text(encoding="utf-8")) or {}
    return [CriteriaCase(**case) for case in data.get("cases", [])]


def _contains(text: str, term: str) -> bool:
    return re.search(re.escape(term.lower()), text.lower()) is not None


def check_case(case: CriteriaCase, text: str) -> list[CheckResult]:
    """Grade one authored criteria text against one case. Order is stable."""
    results: list[CheckResult] = []

    try:
        bodies = parse_criteria(text)
    except CriteriaError as exc:
        return [CheckResult("parses", False, str(exc))]
    results.append(CheckResult("parses", True, f"{len(bodies)} criteria"))

    results.append(CheckResult(
        "bounded", 2 <= len(bodies) <= MAX_CRITERIA,
        f"{len(bodies)} criteria (want 2..{MAX_CRITERIA}: one criterion is a "
        "restatement of the task, not an acceptance test)"))

    joined = "\n".join(bodies).lower()

    hit = [w for w in FAILURE_WORDS
           if re.search(rf"(?<![\w-]){re.escape(w)}\b", joined)]
    results.append(CheckResult(
        "failure_behaviour", bool(hit),
        "no criterion says what must happen when the work cannot be done"
        if not hit else f"covered ({hit[0]})"))

    vague = sorted({w for w in VAGUE if _contains(joined, w)})
    results.append(CheckResult(
        "observable", not vague,
        f"unmeasurable wording: {vague}" if vague else ""))

    impl = sorted({w for w in IMPLEMENTATION if _contains(joined, w)})
    results.append(CheckResult(
        "no_implementation", not impl,
        f"names implementation: {impl}" if impl else ""))

    forbidden = sorted({w for w in case.forbidden if _contains(joined, w)})
    results.append(CheckResult(
        "in_scope", not forbidden,
        f"out of scope or injected: {forbidden}" if forbidden else ""))

    missing = [group for group in case.must_cover
               if not any(_contains(joined, term) for term in group)]
    results.append(CheckResult(
        "covers_task", not missing,
        f"nothing about: {[g[0] for g in missing]}" if missing else ""))

    return results


def evaluate(author: CriteriaAuthor, cases: list[CriteriaCase] | None = None) -> dict:
    """Run the corpus through one author. Report shape mirrors `evalrun`."""
    cases = load_corpus() if cases is None else cases
    report: dict = {"author": "", "cases": [], "passed": True}
    for case in cases:
        try:
            criteria: Criteria = author(case.task_spec, case.residual)
            checks = check_case(case, criteria.text)
            report["author"] = report["author"] or criteria.authored_by
        except CriteriaError as exc:
            checks = [CheckResult("authored", False, str(exc))]
        row = {
            "id": case.id,
            "passed": all(c.passed for c in checks),
            "checks": [{"check": c.check, "passed": c.passed, "detail": c.detail}
                       for c in checks],
        }
        report["cases"].append(row)
        report["passed"] = report["passed"] and row["passed"]
    return report


def render_report(report: dict) -> str:
    return json.dumps(report, sort_keys=True, indent=2) + "\n"


def main() -> int:
    from orchestrator.criteria import select_criteria_author

    report = evaluate(select_criteria_author())
    print(render_report(report), end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":  # pragma: no cover — CLI
    raise SystemExit(main())
