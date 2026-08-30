"""Invocation executor — emits the UI-PLANE §4 event vocabulary.

One emitter, one sink: every event is appended to the trace store, and the
SSE stream is a *view* over that store (the run never depends on a
connection). LangGraph subgraph execution (the POC execution plane for
promoted agents) wraps into this same emitter in Phase 2+; the B1 stub
executor below is the Phase 1 implementation.

`invoke_tool(tool_id, args) -> dict` is the tool-plane seam: tests inject a
fake; `orchestrator.mcpinvoke.mcp_invoker` provides the real MCP client
(optional extra `orchestrator[mcp]`).
"""
from __future__ import annotations

import time
from collections.abc import Callable

from orchestrator.scoring import Scorer
from orchestrator.store import Store

ToolInvoker = Callable[[str, dict], dict]


def _summary(result: dict, limit: int = 120) -> str:
    text = str(result)
    return text if len(text) <= limit else text[:limit] + "…"


def run_invocation(
    store: Store,
    invocation_id: str,
    *,
    agent_id: str,
    agent_version: str,
    model_profile: str,
    catalog_ref: str,
    task_text: str,
    tools: list[dict],
    invoke_tool: ToolInvoker,
    scorer: Scorer,
    supervised: bool = False,
) -> tuple[bool, str]:
    """Execute a (stub-profile) agent run; returns (ok, result summary).

    `supervised` marks a run dispatched under layer 8 supervision (shadow or
    canary) — it never gates behaviour here, it only makes the run observable
    in the trace; the caller (webapp's `_dispatch_supervised`) is what
    actually enforces "quarantined agents run only in supervised mode".

    The caller emits the `terminal` event — anything that must appear in the
    trace (e.g. the B1 registration routing event) happens before terminal,
    which closes the stream."""
    emit = lambda etype, data: store.append_event(invocation_id, etype, data)  # noqa: E731
    emit("invocation", {
        "invocation_id": invocation_id,
        "agent_id": agent_id,
        "version": agent_version,
        "model_profile": model_profile,
        "catalog_ref": catalog_ref,
        "supervised": supervised,
    })

    t0 = time.monotonic()
    emit("node", {"node": "plan", "status": "enter", "elapsed_ms": 0})
    # stub-profile planning: deterministic best lexical match over bindings
    ranked = sorted(
        tools,
        key=lambda t: (-scorer(task_text, t.get("routing_summary", "") + " " +
                               t.get("detail", "")), t["id"]),
    )
    chosen = ranked[0] if ranked else None
    emit("node", {"node": "plan", "status": "exit",
                  "elapsed_ms": int((time.monotonic() - t0) * 1000)})

    if chosen is None:
        return False, "no tools bound"

    t1 = time.monotonic()
    emit("node", {"node": "act", "status": "enter", "elapsed_ms": 0})
    emit("tool_call", {"binding_id": chosen["id"], "tool": chosen["id"],
                       "status": "start", "summary": ""})
    try:
        result = invoke_tool(chosen["id"], {})
        emit("tool_call", {"binding_id": chosen["id"], "tool": chosen["id"],
                           "status": "ok", "summary": _summary(result)})
        ok = True
    except Exception as exc:
        result = {"error": f"{type(exc).__name__}: {exc}"}
        emit("tool_call", {"binding_id": chosen["id"], "tool": chosen["id"],
                           "status": "error", "summary": _summary(result)})
        ok = False
    emit("node", {"node": "act", "status": "exit",
                  "elapsed_ms": int((time.monotonic() - t1) * 1000)})
    return ok, _summary(result, 2000)
