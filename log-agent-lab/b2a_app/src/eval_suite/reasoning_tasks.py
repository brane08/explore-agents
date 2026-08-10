"""
Reasoning tasks for Phase B2b — held-out evaluation of *inferred* tool use.

Unlike the B2a eval cases (which hand the generator the exact tool + arguments
to replay), a ReasoningTask gives the generator only a natural-language
``prompt`` and the tool catalog. The correct tool call (`tool_name` +
`arguments`) is the hidden ground truth — used by the grader, never shown to
the generator. The generator must *infer* the call, so a passing agent
demonstrates generalization rather than memorization.

TRAIN_DEMOS are shown to the generator as few-shot examples (they teach the
output format and tool-selection style). HELDOUT_TASKS are graded; their
prompts are disjoint from the demos and their argument values do not appear in
the demo set, so the model cannot copy an answer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class Demo:
    """A worked example shown to the generator: prompt → correct handling.

    ``tool_name=None`` marks a gap demo: the request cannot be satisfied by
    any available tool and the correct output is a missing-capability report.
    """
    prompt: str
    tool_name: str | None
    arguments: dict | None = None
    missing: str | None = None        # for gap demos: the capability to report


@dataclass(frozen=True)
class ReasoningTask:
    """A held-out task. ``tool_name`` + ``arguments`` are hidden ground truth.

    ``tool_name=None`` marks a **gap task**: no available tool answers the
    request; the correct agent output is {"missing": "<needed capability>"}.
    ``gap_hints`` are alternatives — the reported description must mention at
    least one (case-insensitive) to count as identifying the right gap.
    """
    id: str
    prompt: str                       # natural language — the only thing the generator sees
    tool_name: str | None             # hidden ground truth (None → gap task)
    arguments: dict | None = None     # hidden ground truth
    check: Callable[[dict], bool] | None = None  # optional structural guard on the answer
    gap_hints: tuple[str, ...] = ()   # gap tasks: keywords the report should touch

    @property
    def answerable(self) -> bool:
        return self.tool_name is not None


# Few-shot examples (status_code / WARN patterns) — disjoint from the held-out set.
# Includes one gap demo so the generator learns the missing-capability format.
TRAIN_DEMOS: list[Demo] = [
    Demo("Show me the warning-level log entries.",
         "es_search", {"query_level": "WARN", "size": 10}),
    Demo("Count the log records grouped by HTTP status code.",
         "es_aggregate", {"group_by": "status_code", "metric": "count"}),
    Demo("Give me summary statistics for the status_code field.",
         "field_stats", {"field": "status_code"}),
    Demo("Restart the checkout service.",
         None, missing="No tool can act on services; only log search, "
                       "aggregation, and field statistics are available."),
]

# Graded tasks — canonical tool + args are hidden from the generator.
HELDOUT_TASKS: list[ReasoningTask] = [
    ReasoningTask(
        id="find_errors",
        prompt="List the error-level log entries.",
        tool_name="es_search", arguments={"query_level": "ERROR", "size": 10},
    ),
    ReasoningTask(
        id="volume_by_service",
        prompt="Break down the log volume by service.",
        tool_name="es_aggregate", arguments={"group_by": "service", "metric": "count"},
    ),
    ReasoningTask(
        id="duration_stats",
        prompt="What are the timing statistics for request duration?",
        tool_name="field_stats", arguments={"field": "duration_ms"},
    ),
    ReasoningTask(
        id="level_spread",
        prompt="How varied are the log levels across the records?",
        tool_name="field_stats", arguments={"field": "level"},
    ),
    ReasoningTask(
        id="count_by_severity",
        prompt="Count how many records there are at each severity level.",
        tool_name="es_aggregate", arguments={"group_by": "level", "metric": "count"},
    ),
    ReasoningTask(
        id="info_entries",
        prompt="Fetch the informational log entries.",
        tool_name="es_search", arguments={"query_level": "INFO", "size": 10},
    ),
    # --- gap tasks: no available tool can satisfy these ---
    ReasoningTask(
        id="delete_old_errors",
        prompt="Delete all error logs older than a week.",
        tool_name=None,
        gap_hints=("delete", "remove", "purge", "write", "read-only", "modif"),
    ),
    ReasoningTask(
        id="night_window_logs",
        prompt="Show me the logs recorded between 2am and 3am.",
        tool_name=None,  # es_search exists but has no time-range parameter
        gap_hints=("time", "timestamp", "range", "date", "window", "between", "hour"),
    ),
    ReasoningTask(
        id="live_tail",
        prompt="Stream new log entries as they arrive.",
        tool_name=None,
        gap_hints=("stream", "tail", "live", "real-time", "realtime", "subscribe", "watch"),
    ),
]

TASK_INDEX: dict[str, ReasoningTask] = {t.id: t for t in HELDOUT_TASKS}
ANSWERABLE_TASKS: list[ReasoningTask] = [t for t in HELDOUT_TASKS if t.answerable]
GAP_TASKS: list[ReasoningTask] = [t for t in HELDOUT_TASKS if not t.answerable]
