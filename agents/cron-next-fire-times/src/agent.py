"""Agent skeleton: typed state + nodes for "next N cron fire times in UTC".

No import-time side effects, no model identifiers, no secrets, no bound tools.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

try:  # package-relative when imported as `src.agent`
    from .config import AgentConfig
    from .cron import CronError, CronSchedule, format_utc, parse_timestamp
except ImportError:  # flat import when `src/` is on sys.path
    from config import AgentConfig  # type: ignore[no-redef]
    from cron import (  # type: ignore[no-redef]
        CronError,
        CronSchedule,
        format_utc,
        parse_timestamp,
    )

try:
    from typing import TypedDict
except ImportError:  # pragma: no cover - py<3.8
    TypedDict = dict  # type: ignore[assignment,misc]


class AgentState(TypedDict, total=False):
    cron_expression: str
    start_timestamp: Any
    config: AgentConfig
    schedule: Any
    start_utc: Any
    fire_times: List[str]
    error: Optional[Dict[str, str]]
    result: Dict[str, Any]
    iterations: int


def _fail(state: AgentState, code: str, message: str) -> AgentState:
    state["error"] = {"code": code, "message": message}
    return state


def _config_of(state: AgentState) -> AgentConfig:
    config = state.get("config") or AgentConfig()
    config.validate()
    return config


def validate_input(state: AgentState) -> AgentState:
    """Terminal-on-error node: parse the cron expression and the start timestamp."""
    state["iterations"] = state.get("iterations", 0) + 1
    try:
        config = _config_of(state)
    except ValueError as exc:
        return _fail(state, "INVALID_CONFIG", str(exc))
    state["config"] = config

    expression = state.get("cron_expression")
    if expression is None:
        return _fail(state, "MISSING_INPUT", "cron_expression is required")
    if "start_timestamp" not in state or state.get("start_timestamp") is None:
        return _fail(state, "MISSING_INPUT", "start_timestamp is required")
    try:
        state["schedule"] = CronSchedule(expression)
        state["start_utc"] = parse_timestamp(state["start_timestamp"])
    except CronError as exc:
        return _fail(state, exc.code, exc.message)
    return state


def compute_fire_times(state: AgentState) -> AgentState:
    """Compute exactly `fire_time_count` fire times, or record the failure."""
    state["iterations"] = state.get("iterations", 0) + 1
    if state.get("error"):
        return state
    config = state["config"]
    try:
        moments = state["schedule"].next_fire_times(
            state["start_utc"], config.fire_time_count
        )
    except CronError as exc:
        return _fail(state, exc.code, exc.message)
    state["fire_times"] = format_utc(moments)
    return state


def format_result(state: AgentState) -> AgentState:
    """Build the terminal result envelope for either outcome."""
    state["iterations"] = state.get("iterations", 0) + 1
    config = state.get("config") or AgentConfig()
    error = state.get("error")
    if error:
        state["result"] = {
            "status": "error",
            "error": {"code": error["code"], "message": error["message"]},
        }
        return state
    state["result"] = {
        "status": "ok",
        "cron_expression": state["cron_expression"],
        "start_timestamp": format_utc([state["start_utc"]])[0],
        "timezone": config.output_timezone,
        "fire_times": list(state["fire_times"]),
    }
    return state


def should_continue(state: AgentState) -> str:
    """Conditional edge: stop early on error, otherwise compute."""
    return "format_result" if state.get("error") else "compute_fire_times"


def run(
    cron_expression: Any,
    start_timestamp: Any,
    config: Optional[AgentConfig] = None,
) -> Dict[str, Any]:
    """Entrypoint. Returns the result envelope; never raises for bad input."""
    try:
        from .graph import invoke  # type: ignore[import-not-found]
    except ImportError:
        try:
            from graph import invoke  # type: ignore[no-redef]
        except ImportError:
            invoke = None  # type: ignore[assignment]
    state: AgentState = {
        "cron_expression": cron_expression,
        "start_timestamp": start_timestamp,
        "config": config or AgentConfig(),
        "iterations": 0,
    }
    if invoke is None:
        return run_state(state)["result"]
    return invoke(state)["result"]


def run_state(state: AgentState) -> AgentState:
    """Bounded sequential execution of the node sequence (no external runtime)."""
    config = state.get("config") or AgentConfig()
    budget = getattr(config, "max_node_iterations", 8)
    state = validate_input(state)
    if state.get("iterations", 0) > budget:
        return _fail(state, "BUDGET_EXHAUSTED", "node iteration budget exhausted")
    if should_continue(state) == "compute_fire_times":
        state = compute_fire_times(state)
        if state.get("iterations", 0) > budget:
            return _fail(state, "BUDGET_EXHAUSTED", "node iteration budget exhausted")
    return format_result(state)
