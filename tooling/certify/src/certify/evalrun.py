"""Step 3 — executing the candidate's own `eval/` suite (CHECKLISTS layer 7).

Layer 7 [M]: "`eval/` green on the manifest's `model_profile`, evidence
captured." Certify has required `eval/` to *exist* since the §6 layout check
landed and never ran it, so the one artifact the playbook calls the portability
asset — "model migration = rerun this" — was the only part of a certified
candidate nobody had executed.

Playbook §5 pins the runner contract loosely: "exit 0 = all criteria pass; JSON
per-criterion report; must fail against stub". Two things it leaves open, and
the spec is human-only, so they are invented minimally here and flagged as [H]
review notes — the same precedent as the evalmatrix row `signature` shape and
the content-addressed trace location:

* **Entry point** — `eval/run.py`, executed with the certify interpreter.
* **Report sink** — the path in `$EVAL_REPORT`, else stdout. `$MODEL_PROFILE`
  tells the runner which profile the evidence must be valid for.

"Must fail against stub" is a property of the runner that certify cannot check
without substituting an implementation it does not have; it stays harness-side
(layer 6) and is *not* verified here.

Exit code and report must agree. The exit code is a claim and the report is the
evidence for it, so a runner exiting 0 while reporting a failed criterion is the
exit-code twin of a harness echoing an all-true checklist over violating
artifacts — refused on the same grounds, not resolved in the runner's favour.

**This executes candidate-authored code.** `step_eval` refuses to run behind a
failed static security scan, but a regex scan is not a sandbox: real isolation
(container, no network, dropped credentials) is a deployment concern and is not
implemented here. [H] review note.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from certify.playbook import EVAL_RUNNER_REL

RUNNER_REL = Path(EVAL_RUNNER_REL)
REPORT_ENV = "EVAL_REPORT"
PROFILE_ENV = "MODEL_PROFILE"
TIMEOUT_ENV = "CERTIFY_EVAL_TIMEOUT"
DEFAULT_TIMEOUT = 300.0

# Evidence rides `trace/`, not the candidate root: `trace/` sits outside the
# agent hash scope (CATALOG §4) and promotion already moves it to
# content-addressed storage, so recording evidence neither perturbs the
# certified hash nor needs a second storage mechanism.
EVIDENCE_NAME = "certify-eval.json"


@dataclass
class EvalOutcome:
    """What a runner produced. `exit_code is None` = never reached one."""
    exit_code: int | None
    report: dict | None
    detail: str = ""


# (candidate_path, model_profile) -> EvalOutcome
EvalRunner = Callable[[Path, str], EvalOutcome]


def _timeout() -> float:
    return float(os.environ.get(TIMEOUT_ENV) or DEFAULT_TIMEOUT)


def _tail(text: str, limit: int = 400) -> str:
    text = (text or "").strip()
    return text[-limit:] if len(text) > limit else text


def subprocess_runner(candidate_path: Path, model_profile: str) -> EvalOutcome:
    """Default runner: `python eval/run.py` in the candidate directory."""
    runner = candidate_path / RUNNER_REL
    if not runner.is_file():
        return EvalOutcome(None, None, f"no {RUNNER_REL.as_posix()} in the candidate")

    import tempfile

    with tempfile.TemporaryDirectory(prefix="certify-eval-") as scratch:
        report_path = Path(scratch) / "report.json"
        env = {**os.environ, REPORT_ENV: str(report_path), PROFILE_ENV: model_profile}
        try:
            proc = subprocess.run(
                [sys.executable, str(runner)],
                cwd=candidate_path, env=env, capture_output=True, text=True,
                timeout=_timeout(),
            )
        except subprocess.TimeoutExpired:
            return EvalOutcome(None, None,
                               f"timed out after {_timeout():g}s — a runner that "
                               "cannot finish produces no evidence")

        report = None
        if report_path.is_file():
            raw = report_path.read_text(encoding="utf-8")
        else:
            raw = proc.stdout
        try:
            parsed = json.loads(raw)
            report = parsed if isinstance(parsed, dict) else None
        except (ValueError, TypeError):
            report = None

        return EvalOutcome(proc.returncode, report, _tail(proc.stderr))


def render_evidence(evidence: dict) -> str:
    """Deterministic bytes — this lands in the promotion commit."""
    return json.dumps(evidence, sort_keys=True, indent=2) + "\n"


def persist_eval_evidence(candidate_dir: Path, evidence: dict) -> Path:
    trace_dir = candidate_dir / "trace"
    trace_dir.mkdir(parents=True, exist_ok=True)  # B2a candidates have no trace/
    path = trace_dir / EVIDENCE_NAME
    path.write_text(render_evidence(evidence), encoding="utf-8")
    return path


def load_eval_evidence(candidate_dir: Path) -> dict | None:
    path = Path(candidate_dir) / "trace" / EVIDENCE_NAME
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return loaded if isinstance(loaded, dict) else None
