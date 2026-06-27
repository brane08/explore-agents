"""
Fixed eval cases for the B2a loop.

Each EvalCase calls one MCP tool with fixed arguments and asserts a structural
property of the result. Cases are intentionally simple — the experiment is about
loop convergence, not log-analysis sophistication.

run_cases() executes all cases (or a subset by id) directly against mcp_server
over the network. This is the ground-truth runner used to verify the cases
themselves before any generated agent exists, and also re-used by the evaluator
to judge whether a generated agent's tool calls produced correct output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from fastmcp import Client


@dataclass(frozen=True)
class EvalCase:
    id: str
    tool_name: str
    arguments: dict
    description: str
    check: Callable[[dict], bool]


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    result: dict | None
    error: str | None = None


def _parse_mcp_result(raw: list) -> dict:
    for block in raw:
        if hasattr(block, "text"):
            try:
                return json.loads(block.text)
            except (json.JSONDecodeError, TypeError):
                return {"text": block.text}
    return {"result": str(raw)}


def _all_hits_are_error(result: dict) -> bool:
    hits = result.get("hits", [])
    return len(hits) > 0 and all(h.get("level") == "ERROR" for h in hits)


def _has_service_buckets(result: dict) -> bool:
    buckets = result.get("buckets", [])
    return len(buckets) > 0 and all(b.get("count", 0) > 0 for b in buckets)


def _has_numeric_stats(result: dict) -> bool:
    return all(k in result for k in ("min", "max", "avg", "count"))


def _has_categorical_stats(result: dict) -> bool:
    return "cardinality" in result and int(result["cardinality"]) > 0


CASES: list[EvalCase] = [
    EvalCase(
        id="search_error_level",
        tool_name="es_search",
        arguments={"query_level": "ERROR", "size": 10},
        description="es_search filtered by ERROR returns only ERROR-level records",
        check=_all_hits_are_error,
    ),
    EvalCase(
        id="aggregate_by_service",
        tool_name="es_aggregate",
        arguments={"group_by": "service", "metric": "count"},
        description="es_aggregate by service returns non-empty buckets with positive counts",
        check=_has_service_buckets,
    ),
    EvalCase(
        id="field_stats_numeric",
        tool_name="field_stats",
        arguments={"field": "duration_ms"},
        description="field_stats on duration_ms returns min/max/avg/count",
        check=_has_numeric_stats,
    ),
    EvalCase(
        id="field_stats_categorical",
        tool_name="field_stats",
        arguments={"field": "level"},
        description="field_stats on level returns cardinality > 0",
        check=_has_categorical_stats,
    ),
]

CASE_INDEX: dict[str, EvalCase] = {c.id: c for c in CASES}


async def run_cases(
    mcp_server_url: str,
    case_ids: list[str] | None = None,
) -> list[CaseResult]:
    """Call each selected case directly against mcp_server and return results."""
    url = mcp_server_url.rstrip("/") + "/mcp"
    selected = [CASE_INDEX[cid] for cid in case_ids] if case_ids else CASES
    results: list[CaseResult] = []
    async with Client(url) as client:
        for case in selected:
            try:
                raw = await client.call_tool(case.tool_name, case.arguments)
                parsed = _parse_mcp_result(raw)
                passed = case.check(parsed)
                results.append(CaseResult(case_id=case.id, passed=passed, result=parsed))
            except Exception as exc:
                results.append(CaseResult(case_id=case.id, passed=False, result=None, error=str(exc)))
    return results
