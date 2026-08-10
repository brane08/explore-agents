from __future__ import annotations

import asyncio
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the workspace root (log-agent-lab/) if present
load_dotenv(Path(__file__).parents[3] / ".env")

from fastapi import FastAPI, HTTPException, Request
from fastmcp import Client
from pydantic import BaseModel, Field

from eval_suite.log_analysis_cases import CASE_INDEX, CASES
from eval_suite.reasoning_tasks import HELDOUT_TASKS, TASK_INDEX
from loop.llm import select_completer
from loop.orchestrator import LoopReport, run_loop
from loop.reasoning import SuiteReport, run_reasoning_suite
from loop.skills import load_skill_tools
from loop.spec import EvalCaseSpec, TaskSpec, ToolSchema


# ---------------------------------------------------------------------------
# Lifespan — discover available tools once at startup.
# Skills directory first (works with no live MCP registry); MCP fallback.
# ---------------------------------------------------------------------------

_available_tools: list[ToolSchema] = []


def _skills_dir() -> Path:
    default = Path(__file__).parents[3]  # platform catalog root (skills/ + mcp/)
    return Path(os.environ.get("B2A_SKILLS_DIR", str(default)))


async def _discover_from_mcp() -> list[ToolSchema]:
    mcp_url = os.environ.get("MCP_SERVER_URL", "http://localhost:8000").rstrip("/") + "/mcp"
    async with Client(mcp_url) as client:
        tools = await client.list_tools()
    return [
        ToolSchema(
            name=t.name,
            description=t.description or "",
            input_schema=t.inputSchema.model_dump()
            if hasattr(t.inputSchema, "model_dump")
            else dict(t.inputSchema),
        )
        for t in tools
    ]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _available_tools
    _available_tools = load_skill_tools(_skills_dir())
    if not _available_tools:
        try:
            _available_tools = await _discover_from_mcp()
        except Exception:
            _available_tools = []
    yield


app = FastAPI(title="b2a-app", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class RunLoopRequest(BaseModel):
    task_description: str = Field(
        default="Analyse log records: search for errors, aggregate by service, compute field stats.",
    )
    eval_case_ids: list[str] = Field(
        default_factory=lambda: [c.id for c in CASES],
        description="Subset of eval case ids to run. Defaults to all cases.",
    )
    max_iterations: int = Field(default=5, ge=1, le=20)
    sandbox_timeout: int = Field(default=30, ge=5, le=120)


class CaseResultOut(BaseModel):
    case_id: str
    passed: bool
    detail: str


class RunLoopResponse(BaseModel):
    stopped_reason: str
    iterations_run: int
    final_score: float
    trend: list[float]
    case_results: list[CaseResultOut]
    final_agent_path: str | None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "tools_loaded": len(_available_tools)}


@app.get("/tools")
def list_tools() -> dict:
    """List the discovered tool catalog (from the skills manifest or MCP)."""
    return {"tools": [t.model_dump() for t in _available_tools]}


@app.post("/run-loop", response_model=RunLoopResponse)
async def run_loop_endpoint(req: RunLoopRequest, request: Request) -> RunLoopResponse:
    if not _available_tools:
        raise HTTPException(
            status_code=503,
            detail="No tools available — populate the skills directory or start mcp_server.",
        )

    unknown = [cid for cid in req.eval_case_ids if cid not in CASE_INDEX]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown eval case ids: {unknown}")

    eval_cases = [
        EvalCaseSpec(id=c.id, tool_name=c.tool_name, arguments=c.arguments)
        for cid in req.eval_case_ids
        for c in [CASE_INDEX[cid]]
    ]

    mcp_url = os.environ.get("MCP_SERVER_URL", "http://localhost:8000")
    spec = TaskSpec(
        task_description=req.task_description,
        mcp_server_url=mcp_url,
        available_tools=list(_available_tools),
        eval_case_ids=req.eval_case_ids,
        eval_cases=eval_cases,
        max_iterations=req.max_iterations,
    )

    cancel_event = threading.Event()

    async def _watch_disconnect() -> None:
        while not cancel_event.is_set():
            if await request.is_disconnected():
                cancel_event.set()
                return
            await asyncio.sleep(1)

    watcher = asyncio.create_task(_watch_disconnect())
    try:
        report: LoopReport = await asyncio.to_thread(
            run_loop, spec, sandbox_timeout=req.sandbox_timeout, cancel_event=cancel_event
        )
    finally:
        cancel_event.set()
        watcher.cancel()

    last_case_results: list[CaseResultOut] = []
    if report.records:
        last_eval = report.records[-1].eval_result
        if last_eval:
            last_case_results = [
                CaseResultOut(case_id=r.case_id, passed=r.passed, detail=r.detail)
                for r in last_eval.case_results
            ]

    return RunLoopResponse(
        stopped_reason=report.stopped_reason,
        iterations_run=report.iterations_run,
        final_score=report.final_score,
        trend=report.trend,
        case_results=last_case_results,
        final_agent_path=str(report.final_path) if report.final_path else None,
    )


# ---------------------------------------------------------------------------
# B2b — reasoning over held-out tasks
# ---------------------------------------------------------------------------

# Indirection so tests can inject a deterministic completer.
_completer_factory = select_completer


class RunReasoningRequest(BaseModel):
    task_ids: list[str] = Field(
        default_factory=lambda: [t.id for t in HELDOUT_TASKS],
        description="Subset of held-out task ids to run. Defaults to all.",
    )
    max_attempts: int = Field(default=3, ge=1, le=10)
    sandbox_timeout: int = Field(default=60, ge=5, le=120)


class TaskReportOut(BaseModel):
    task_id: str
    passed: bool
    attempts: int
    detail: str


class RunReasoningResponse(BaseModel):
    passed: int
    total: int
    pass_rate: float
    answerable_passed: int
    answerable_total: int
    gaps_detected: int
    gap_total: int
    missing_capabilities: list[str]
    tasks: list[TaskReportOut]


@app.post("/run-reasoning", response_model=RunReasoningResponse)
def run_reasoning_endpoint(req: RunReasoningRequest) -> RunReasoningResponse:
    if not _available_tools:
        raise HTTPException(
            status_code=503,
            detail="No tools available — populate the skills directory or start mcp_server.",
        )

    unknown = [tid for tid in req.task_ids if tid not in TASK_INDEX]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown task ids: {unknown}")

    completer = _completer_factory()
    if completer is None:
        raise HTTPException(
            status_code=503,
            detail="No LLM provider configured — reasoning tasks require one "
                   "(set B2A_LLM_PROVIDER / credentials).",
        )

    mcp_url = os.environ.get("MCP_SERVER_URL", "http://localhost:8000")
    base_dir = Path(__file__).parents[2] / "generated" / "reasoning"
    report: SuiteReport = run_reasoning_suite(
        [TASK_INDEX[tid] for tid in req.task_ids],
        _available_tools,
        mcp_url,
        completer,
        base_dir,
        max_attempts=req.max_attempts,
        sandbox_timeout=req.sandbox_timeout,
    )
    return RunReasoningResponse(
        passed=report.passed,
        total=report.total,
        pass_rate=report.pass_rate,
        answerable_passed=report.answerable_passed,
        answerable_total=report.answerable_total,
        gaps_detected=report.gaps_detected,
        gap_total=report.gap_total,
        missing_capabilities=report.missing_capabilities,
        tasks=[
            TaskReportOut(
                task_id=r.task_id,
                passed=r.passed,
                attempts=r.attempts,
                detail=r.results[-1].detail if r.results else "",
            )
            for r in report.reports
        ],
    )
