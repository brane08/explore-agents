"""
reasoning.py — Phase B2b loop: generate an agent that *infers* its tool calls
from a natural-language task, then grade it against hidden ground truth.

Flow for one held-out task:

    build_reasoning_prompt(task, catalog, demos)   # NL prompt + catalog + few-shot
        -> completer(prompt)                        # generator infers the plan
        -> agent.py (static, no model at runtime)
        -> run_in_sandbox                           # deterministic, key-free
        -> grade(task, stdout)                      # compare answer to live ground truth

The generator never sees the task's canonical tool/args — only the graders do.
`render_answer_agent` is the reference agent shape (also used as a test oracle).
"""
from __future__ import annotations

import ast
import json
import textwrap
from dataclasses import dataclass
from pathlib import Path

import anyio
from fastmcp import Client

from eval_suite.log_analysis_cases import _parse_mcp_result
from eval_suite.reasoning_tasks import TRAIN_DEMOS, Demo, ReasoningTask
from loop.generator import _strip_fences
from loop.llm import Completer
from loop.sandbox_runner import run_in_sandbox
from loop.spec import ToolSchema


# Symmetric failure message — must not reveal whether the task is answerable
# or a capability gap; the generator has to reconsider both possibilities.
_WRONG = "output does not match the correct handling of this request"


@dataclass
class TaskResult:
    task_id: str
    passed: bool
    detail: str
    answer: dict | None
    agent_path: Path | None
    missing: str | None = None        # the agent's missing-capability report, if any


# ---------------------------------------------------------------------------
# Prompt — task + catalog + few-shot demos. Never includes the task's own call.
# ---------------------------------------------------------------------------

def build_reasoning_prompt(
    task: ReasoningTask,
    catalog: list[ToolSchema],
    demos: list[Demo] = TRAIN_DEMOS,
    feedback: str = "",
) -> str:
    tools = json.dumps(
        [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
         for t in catalog],
        indent=2,
    )
    examples = "\n".join(
        f'- Request: "{d.prompt}"\n  Correct call: '
        f'{json.dumps({"tool": d.tool_name, "arguments": d.arguments})}'
        if d.tool_name is not None
        else f'- Request: "{d.prompt}"\n  Correct output: '
             f'{json.dumps({"missing": d.missing})}'
        for d in demos
    )
    feedback_section = (
        f"\n\nA previous attempt at this request failed. Feedback:\n{feedback}\n"
        "Reconsider which tool and arguments actually answer the request."
        if feedback.strip() else ""
    )
    return textwrap.dedent(f"""\
        Generate a complete, runnable Python file: an agent that answers the request
        below by choosing and calling the right MCP tool. You must infer which tool
        to call and with what arguments — decide from the request and the tool
        catalog. Do not ask; produce working code.

        Request:
        {task.prompt}

        Available tools (JSON schema):
        {tools}

        Worked examples (request → the call that answers it):
        {examples}

        Requirements:
        - Call the tool via fastmcp.Client at:
          MCP_URL = os.environ.get("MCP_SERVER_URL", "").rstrip("/") + "/mcp"
        - client.call_tool(...) returns a CallToolResult; read text from
          result.content (list of blocks, each with a .text attribute).
        - Wrap the call so a failure records {{"error": "..."}} instead of raising.
        - Print exactly one line to stdout: json.dumps({{"answer": <parsed tool result>}}).
        - IF AND ONLY IF no available tool can satisfy the request, do not invent
          or approximate a call — print json.dumps({{"missing": "<one sentence
          describing the capability that would be needed>"}}) instead. When a
          suitable tool exists, you must call it, never claim a gap.
        - Self-contained, runnable as: python agent.py. No markdown fences.
        {feedback_section}
        Return ONLY the Python source code.
    """)


# ---------------------------------------------------------------------------
# Reference agent (also the test oracle): call one tool, print {"answer": ...}
# ---------------------------------------------------------------------------

def render_answer_agent(tool_name: str, arguments: dict, mcp_server_url: str) -> str:
    args_repr = json.dumps(arguments)
    return textwrap.dedent(f"""\
        import asyncio
        import json
        import os

        from fastmcp import Client

        MCP_URL = os.environ.get("MCP_SERVER_URL", {mcp_server_url!r}).rstrip("/") + "/mcp"


        async def main() -> None:
            answer = {{"error": "no result"}}
            try:
                async with Client(MCP_URL) as client:
                    raw = await client.call_tool({tool_name!r}, {args_repr})
                for block in raw.content:
                    if hasattr(block, "text"):
                        try:
                            answer = json.loads(block.text)
                        except (json.JSONDecodeError, TypeError):
                            answer = {{"text": block.text}}
                        break
            except Exception as exc:
                answer = {{"error": f"{{type(exc).__name__}}: {{exc}}"}}
            print(json.dumps({{"answer": answer}}))


        if __name__ == "__main__":
            asyncio.run(main())
    """)


def render_missing_agent(description: str) -> str:
    """Reference agent for a gap task: report the missing capability."""
    return textwrap.dedent(f"""\
        import json

        if __name__ == "__main__":
            print(json.dumps({{"missing": {description!r}}}))
    """)


def oracle_completer(task: ReasoningTask, mcp_server_url: str) -> Completer:
    """A perfect-reasoner stand-in: returns the correct agent for `task`,
    ignoring the prompt. Lets the harness be tested without a real model."""
    def complete(_prompt: str) -> str:
        if not task.answerable:
            hint = task.gap_hints[0] if task.gap_hints else "capability"
            return render_missing_agent(
                f"No available tool provides the required {hint} capability."
            )
        return render_answer_agent(task.tool_name, task.arguments, mcp_server_url)
    return complete


# ---------------------------------------------------------------------------
# Grading — compute ground truth live, compare the agent's answer to it
# ---------------------------------------------------------------------------

async def _ground_truth(task: ReasoningTask, mcp_server_url: str) -> dict:
    url = mcp_server_url.rstrip("/") + "/mcp"
    async with Client(url) as client:
        raw = await client.call_tool(task.tool_name, task.arguments)
    return _parse_mcp_result(raw)


def grade(
    task: ReasoningTask, stdout: str, mcp_server_url: str
) -> tuple[bool, dict | None, str, str | None]:
    """Grade agent stdout. Returns (passed, answer, detail, missing_report).

    Symmetric on purpose: fabricating an answer on a gap task and claiming a
    gap on an answerable task both fail with the same generic detail, so
    feedback never tells the generator which kind of task it is facing.
    """
    try:
        output = json.loads(stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        return False, None, f"stdout not valid JSON: {exc}", None
    if not isinstance(output, dict):
        return False, None, "stdout is not a JSON object", None

    missing = output.get("missing")
    answer = output.get("answer")

    if not task.answerable:
        # correct behaviour: a missing-capability report touching the right gap
        if not isinstance(missing, str) or not missing.strip():
            return False, answer, _WRONG, None
        if task.gap_hints and not any(h.lower() in missing.lower() for h in task.gap_hints):
            return False, answer, _WRONG, missing
        return True, None, "", missing

    # answerable task: a gap claim is a failure like any wrong answer
    if missing is not None:
        return False, answer, _WRONG, str(missing)

    if task.check is not None and not task.check(answer):
        return False, answer, "structural check failed", None
    try:
        truth = anyio.run(_ground_truth, task, mcp_server_url)
    except Exception as exc:
        return False, answer, f"could not compute ground truth: {type(exc).__name__}: {exc}", None
    if answer == truth:
        return True, answer, "", None
    return False, answer, _WRONG, None


# ---------------------------------------------------------------------------
# One held-out task, end to end
# ---------------------------------------------------------------------------

def run_reasoning_task(
    task: ReasoningTask,
    catalog: list[ToolSchema],
    mcp_server_url: str,
    completer: Completer,
    generated_base_dir: Path,
    sandbox_timeout: int = 60,
    feedback: str = "",
) -> TaskResult:
    prompt = build_reasoning_prompt(task, catalog, feedback=feedback)
    source = _strip_fences(completer(prompt))
    try:
        ast.parse(source)
    except SyntaxError as exc:
        return TaskResult(task.id, False, f"generated code invalid: {exc}", None, None)

    out_dir = generated_base_dir / task.id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "agent.py"
    path.write_text(source, encoding="utf-8")

    sandbox = run_in_sandbox(path, mcp_server_url, timeout_seconds=sandbox_timeout)
    if sandbox.timed_out:
        return TaskResult(task.id, False, "sandbox timed out", None, path)
    if sandbox.exit_code != 0:
        return TaskResult(task.id, False, f"agent crashed: {(sandbox.stderr or '')[:200]}", None, path)

    passed, answer, detail, missing = grade(task, sandbox.stdout, mcp_server_url)
    return TaskResult(task.id, passed, detail, answer, path, missing=missing)


# ---------------------------------------------------------------------------
# Feedback → retry loop over one task, and a held-out suite report
# ---------------------------------------------------------------------------

@dataclass
class TaskReport:
    task_id: str
    passed: bool
    attempts: int
    results: list[TaskResult]


@dataclass
class SuiteReport:
    passed: int
    total: int
    reports: list[TaskReport]
    answerable_passed: int = 0
    answerable_total: int = 0
    gaps_detected: int = 0
    gap_total: int = 0
    missing_capabilities: list[str] = None  # gap reports from passing gap tasks

    def __post_init__(self) -> None:
        if self.missing_capabilities is None:
            self.missing_capabilities = []

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


def run_reasoning_loop(
    task: ReasoningTask,
    catalog: list[ToolSchema],
    mcp_server_url: str,
    completer: Completer,
    generated_base_dir: Path,
    max_attempts: int = 3,
    sandbox_timeout: int = 60,
) -> TaskReport:
    """Retry a held-out task with feedback until it passes or attempts run out.

    Feedback describes *what went wrong* (wrong answer, crash, bad output) but
    never reveals the canonical call — the generator must still infer it.
    """
    results: list[TaskResult] = []
    feedback = ""
    for attempt in range(max_attempts):
        result = run_reasoning_task(
            task, catalog, mcp_server_url, completer,
            generated_base_dir / f"attempt_{attempt}",
            sandbox_timeout=sandbox_timeout, feedback=feedback,
        )
        results.append(result)
        if result.passed:
            return TaskReport(task.id, True, attempt + 1, results)
        feedback = (
            f"Attempt {attempt} failed: {result.detail or 'wrong answer'}. "
            f"Your agent's answer was: {json.dumps(result.answer)[:300]}"
            if result.answer is not None
            else f"Attempt {attempt} failed: {result.detail}"
        )
    return TaskReport(task.id, False, max_attempts, results)


def run_reasoning_suite(
    tasks: list[ReasoningTask],
    catalog: list[ToolSchema],
    mcp_server_url: str,
    completer: Completer,
    generated_base_dir: Path,
    max_attempts: int = 3,
    sandbox_timeout: int = 60,
) -> SuiteReport:
    """Run every held-out task through the feedback loop.

    Reports overall + per-kind pass rates and aggregates the missing-capability
    descriptions from correctly-detected gaps — the actionable "what should be
    added to the tool catalog" highlight.
    """
    reports = [
        run_reasoning_loop(
            t, catalog, mcp_server_url, completer,
            generated_base_dir / t.id,
            max_attempts=max_attempts, sandbox_timeout=sandbox_timeout,
        )
        for t in tasks
    ]
    by_id = {t.id: t for t in tasks}
    answerable = [r for r in reports if by_id[r.task_id].answerable]
    gaps = [r for r in reports if not by_id[r.task_id].answerable]
    return SuiteReport(
        passed=sum(1 for r in reports if r.passed),
        total=len(reports),
        reports=reports,
        answerable_passed=sum(1 for r in answerable if r.passed),
        answerable_total=len(answerable),
        gaps_detected=sum(1 for r in gaps if r.passed),
        gap_total=len(gaps),
        missing_capabilities=[
            r.results[-1].missing
            for r in gaps
            if r.passed and r.results and r.results[-1].missing
        ],
    )
