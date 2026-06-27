"""
orchestrator.py — Step 6 of the B2a loop.

Flat, bounded loop: generate → sandbox → evaluate → decide.

Stop conditions (in priority order):
  1. First full eval-suite pass → stopped_reason="passed"
  2. Sandbox timeout           → stopped_reason="timeout"   (not retried)
  3. Sandbox crash             → stopped_reason="crash"     (not retried)
  4. Bad JSON from agent       → stopped_reason="bad_json"  (not retried)
  5. ast.parse failure         → stopped_reason="syntax_error"
  6. Uncaught exception        → stopped_reason="exception"
  7. Iteration ceiling hit     → stopped_reason="ceiling"

Only eval failures (wrong output, partial score) advance to the next
iteration. All other failures stop immediately and are reported — they
are architectural findings, not bugs to paper over.

The exact prompt sent at each iteration is written alongside the
generated file as iteration_<n>_prompt.txt for post-run inspection.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from loop.evaluator import EvalResult, evaluate
from loop.generator import GenerationResult, GeneratorFunc, generate_candidate
from loop.sandbox_runner import SandboxResult, run_in_sandbox
from loop.spec import TaskSpec


@dataclass
class IterationRecord:
    iteration: int
    generated_path: Path | None
    sandbox: SandboxResult | None
    eval_result: EvalResult | None
    prompt_sent: str
    feedback_used: str


@dataclass
class LoopReport:
    stopped_reason: str          # see module docstring for values
    iterations_run: int
    final_score: float
    trend: list[float]           # score per completed iteration
    records: list[IterationRecord]
    final_path: Path | None = None
    error_detail: str = ""


def _build_feedback(eval_result: EvalResult, iteration: int) -> str:
    lines = [
        f"Iteration {iteration} failed. Score: {eval_result.score:.2f}.",
        "Failed cases:",
    ]
    for r in eval_result.case_results:
        if not r.passed:
            lines.append(f"  - {r.case_id}: {r.detail or 'check returned False'}")
    if eval_result.stop_reason:
        lines.append(f"Stop reason: {eval_result.stop_reason}")
    return "\n".join(lines)


def _write_prompt_log(gen_result: GenerationResult, feedback: str) -> None:
    log_path = gen_result.path.parent / f"iteration_{gen_result.iteration}_prompt.txt"
    log_path.write_text(
        f"=== PROMPT SENT ===\n{gen_result.prompt_sent}\n\n"
        f"=== FEEDBACK IN ===\n{feedback or '(none — first iteration)'}\n",
        encoding="utf-8",
    )


def run_loop(
    spec: TaskSpec,
    generator_fn: GeneratorFunc | None = None,
    sandbox_timeout: int = 30,
    generated_base_dir: Path | None = None,
) -> LoopReport:
    """
    Execute the B2a generate→sandbox→evaluate loop.

    Returns a LoopReport regardless of outcome. Never raises.
    """
    trend: list[float] = []
    records: list[IterationRecord] = []
    feedback = ""

    for iteration in range(spec.max_iterations):
        gen_result: GenerationResult | None = None
        sandbox: SandboxResult | None = None
        eval_result: EvalResult | None = None
        prompt_sent = ""

        try:
            # --- generate ---
            try:
                gen_result = generate_candidate(
                    spec=spec,
                    iteration=iteration,
                    feedback=feedback,
                    generator_fn=generator_fn,
                    generated_base_dir=generated_base_dir,
                )
                prompt_sent = gen_result.prompt_sent
                _write_prompt_log(gen_result, feedback)
            except SyntaxError as exc:
                records.append(IterationRecord(
                    iteration=iteration, generated_path=None, sandbox=None,
                    eval_result=None, prompt_sent=prompt_sent, feedback_used=feedback,
                ))
                return LoopReport(
                    stopped_reason="syntax_error",
                    iterations_run=iteration + 1,
                    final_score=trend[-1] if trend else 0.0,
                    trend=trend, records=records,
                    error_detail=str(exc),
                )

            # --- sandbox ---
            sandbox = run_in_sandbox(
                agent_path=gen_result.path,
                mcp_server_url=spec.mcp_server_url,
                timeout_seconds=sandbox_timeout,
            )

            # --- evaluate ---
            eval_result = evaluate(sandbox, list(spec.eval_cases))
            trend.append(eval_result.score)
            records.append(IterationRecord(
                iteration=iteration,
                generated_path=gen_result.path,
                sandbox=sandbox,
                eval_result=eval_result,
                prompt_sent=prompt_sent,
                feedback_used=feedback,
            ))

            # --- decide ---
            if eval_result.passed:
                return LoopReport(
                    stopped_reason="passed",
                    iterations_run=iteration + 1,
                    final_score=1.0,
                    trend=trend, records=records,
                    final_path=gen_result.path,
                )

            if eval_result.stop_reason in ("timeout", "crash", "bad_json"):
                return LoopReport(
                    stopped_reason=eval_result.stop_reason,
                    iterations_run=iteration + 1,
                    final_score=eval_result.score,
                    trend=trend, records=records,
                    final_path=gen_result.path,
                    error_detail=eval_result.stop_reason,
                )

            feedback = _build_feedback(eval_result, iteration)

        except Exception as exc:
            records.append(IterationRecord(
                iteration=iteration,
                generated_path=gen_result.path if gen_result else None,
                sandbox=sandbox,
                eval_result=eval_result,
                prompt_sent=prompt_sent,
                feedback_used=feedback,
            ))
            return LoopReport(
                stopped_reason="exception",
                iterations_run=iteration + 1,
                final_score=trend[-1] if trend else 0.0,
                trend=trend, records=records,
                error_detail=traceback.format_exc(),
            )

    # ceiling reached
    return LoopReport(
        stopped_reason="ceiling",
        iterations_run=spec.max_iterations,
        final_score=trend[-1] if trend else 0.0,
        trend=trend, records=records,
        final_path=records[-1].generated_path if records else None,
    )
