from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the workspace root (log-agent-lab/) if present
load_dotenv(Path(__file__).parents[3] / ".env")

from fastapi import FastAPI, HTTPException
from fastmcp import Client
from pydantic import BaseModel, Field

from eval_suite.log_analysis_cases import CASES
from loop.orchestrator import LoopReport, run_loop
from loop.spec import EvalCaseSpec, TaskSpec, ToolSchema


# ---------------------------------------------------------------------------
# Lifespan — fetch available tools from MCP server once at startup
# ---------------------------------------------------------------------------

_available_tools: list[ToolSchema] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _available_tools
    mcp_url = os.environ.get("MCP_SERVER_URL", "http://localhost:8000").rstrip("/") + "/mcp"
    try:
        async with Client(mcp_url) as client:
            tools = await client.list_tools()
        _available_tools = [
            ToolSchema(
                name=t.name,
                description=t.description or "",
                input_schema=t.inputSchema.model_dump()
                if hasattr(t.inputSchema, "model_dump")
                else dict(t.inputSchema),
            )
            for t in tools
        ]
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


@app.post("/run-loop", response_model=RunLoopResponse)
async def run_loop_endpoint(req: RunLoopRequest) -> RunLoopResponse:
    if not _available_tools:
        raise HTTPException(status_code=503, detail="MCP server tools not loaded — is mcp_server running?")

    known_ids = {c.id for c in CASES}
    unknown = [cid for cid in req.eval_case_ids if cid not in known_ids]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown eval case ids: {unknown}")

    eval_cases = [
        EvalCaseSpec(id=c.id, tool_name=c.tool_name, arguments=c.arguments)
        for c in CASES
        if c.id in req.eval_case_ids
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

    report: LoopReport = run_loop(spec, sandbox_timeout=req.sandbox_timeout)

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
