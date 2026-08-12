"""Graph assembly (LangGraph when available, deterministic fallback otherwise).

    validate_input --(error)--> format_result --> END
                   \\--(ok)---> compute_fire_times --> format_result --> END

Every path terminates; there are no cycles, so the iteration guard in
`AgentConfig.max_node_iterations` is a hard upper bound.
"""

from __future__ import annotations

from typing import Any

try:
    from .agent import (
        AgentState,
        compute_fire_times,
        format_result,
        run_state,
        should_continue,
        validate_input,
    )
except ImportError:  # flat import when `src/` is on sys.path
    from agent import (  # type: ignore[no-redef]
        AgentState,
        compute_fire_times,
        format_result,
        run_state,
        should_continue,
        validate_input,
    )


def build_graph() -> Any:
    """Return a compiled LangGraph app, or None when LangGraph is unavailable."""
    try:
        from langgraph.graph import END, StateGraph
    except ImportError:
        return None
    builder = StateGraph(AgentState)
    builder.add_node("validate_input", validate_input)
    builder.add_node("compute_fire_times", compute_fire_times)
    builder.add_node("format_result", format_result)
    builder.set_entry_point("validate_input")
    builder.add_conditional_edges(
        "validate_input",
        should_continue,
        {"compute_fire_times": "compute_fire_times", "format_result": "format_result"},
    )
    builder.add_edge("compute_fire_times", "format_result")
    builder.add_edge("format_result", END)
    return builder.compile()


def invoke(state: "AgentState") -> "AgentState":
    app = build_graph()
    if app is None:
        return run_state(state)
    return app.invoke(state)
