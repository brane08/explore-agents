"""
Tests for orchestrator.py.

Uses injected generator_fn and sandbox mocking so no network or API key needed.
The loop logic (termination, trend, feedback propagation) is fully testable offline.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from loop.evaluator import CaseResult, EvalResult
from loop.generator import GenerationResult
from loop.orchestrator import LoopReport, run_loop
from loop.sandbox_runner import SandboxResult
from loop.spec import EvalCaseSpec, TaskSpec, ToolSchema

# --- fixtures ---

_TOOL = ToolSchema(name="es_search", description="Search", input_schema={"type": "object"})
_CASE = EvalCaseSpec(id="search_error_level", tool_name="es_search",
                     arguments={"query_level": "ERROR", "size": 10})

_GOOD_OUTPUT = json.dumps({"search_error_level": {"hits": [{"level": "ERROR"}], "total": 1}})
_BAD_OUTPUT = json.dumps({"search_error_level": {"hits": [{"level": "INFO"}], "total": 1}})


def _make_spec(max_iterations: int = 3) -> TaskSpec:
    return TaskSpec(
        task_description="test task",
        mcp_server_url="http://localhost:8000",
        available_tools=[_TOOL],
        eval_case_ids=["search_error_level"],
        eval_cases=[_CASE],
        max_iterations=max_iterations,
    )


_GOOD_AGENT_SRC = (
    "import json\n"
    "print(json.dumps({'search_error_level': {'hits': [{'level': 'ERROR'}], 'total': 1}}))\n"
)

_BAD_AGENT_SRC = (
    "import json\n"
    "print(json.dumps({'search_error_level': {'hits': [{'level': 'INFO'}], 'total': 1}}))\n"
)


def _good_generator(spec, feedback):
    return "prompt", _GOOD_AGENT_SRC


def _bad_generator(spec, feedback):
    return "prompt", _BAD_AGENT_SRC


def _syntax_error_generator(spec, feedback):
    return "prompt", "def broken(:\n    pass"


def _crash_generator(spec, feedback):
    return "prompt", "raise RuntimeError('intentional crash')\n"


def _timeout_generator(spec, feedback):
    return "prompt", "import time\ntime.sleep(60)\n"


# --- loop terminates on pass ---

def test_stops_on_first_pass(tmp_path):
    report = run_loop(_make_spec(), generator_fn=_good_generator,
                      generated_base_dir=tmp_path)
    assert report.stopped_reason == "passed"
    assert report.iterations_run == 1
    assert report.final_score == 1.0
    assert report.trend == [1.0]


def test_final_path_set_on_pass(tmp_path):
    report = run_loop(_make_spec(), generator_fn=_good_generator,
                      generated_base_dir=tmp_path)
    assert report.final_path is not None
    assert report.final_path.exists()


# --- loop hits ceiling ---

def test_ceiling_reached_when_never_passes(tmp_path):
    report = run_loop(_make_spec(max_iterations=3), generator_fn=_bad_generator,
                      generated_base_dir=tmp_path)
    assert report.stopped_reason == "ceiling"
    assert report.iterations_run == 3
    assert report.final_score == 0.0


def test_trend_has_one_entry_per_iteration(tmp_path):
    report = run_loop(_make_spec(max_iterations=3), generator_fn=_bad_generator,
                      generated_base_dir=tmp_path)
    assert len(report.trend) == 3


def test_records_has_one_entry_per_iteration(tmp_path):
    report = run_loop(_make_spec(max_iterations=3), generator_fn=_bad_generator,
                      generated_base_dir=tmp_path)
    assert len(report.records) == 3


# --- early stops ---

def test_stops_on_syntax_error(tmp_path):
    report = run_loop(_make_spec(), generator_fn=_syntax_error_generator,
                      generated_base_dir=tmp_path)
    assert report.stopped_reason == "syntax_error"
    assert report.iterations_run == 1
    assert report.error_detail != ""


def test_stops_on_sandbox_crash(tmp_path):
    report = run_loop(_make_spec(), generator_fn=_crash_generator,
                      generated_base_dir=tmp_path)
    assert report.stopped_reason == "crash"
    assert report.iterations_run == 1


def test_stops_on_timeout(tmp_path):
    report = run_loop(_make_spec(), generator_fn=_timeout_generator,
                      sandbox_timeout=1, generated_base_dir=tmp_path)
    assert report.stopped_reason == "timeout"
    assert report.iterations_run == 1


# --- feedback propagation ---

def test_feedback_injected_on_second_iteration(tmp_path):
    received_feedbacks: list[str] = []

    def tracking_generator(spec, feedback):
        received_feedbacks.append(feedback)
        return _bad_generator(spec, feedback)

    run_loop(_make_spec(max_iterations=2), generator_fn=tracking_generator,
             generated_base_dir=tmp_path)

    assert received_feedbacks[0] == ""                    # first call: no feedback
    assert "Failed cases" in received_feedbacks[1]        # second call: has feedback


# --- prompt log written ---

def test_prompt_log_written(tmp_path):
    run_loop(_make_spec(max_iterations=1), generator_fn=_bad_generator,
             generated_base_dir=tmp_path)
    logs = list(tmp_path.rglob("*_prompt.txt"))
    assert len(logs) == 1
    assert "PROMPT SENT" in logs[0].read_text()


# --- exception in loop ---

def test_exception_in_generator_returns_report(tmp_path):
    def exploding_generator(spec, feedback):
        raise ValueError("unexpected boom")

    report = run_loop(_make_spec(), generator_fn=exploding_generator,
                      generated_base_dir=tmp_path)
    assert report.stopped_reason == "exception"
    assert "unexpected boom" in report.error_detail


# --- loop report structure ---

def test_loop_report_never_raises(tmp_path):
    for gen in [_good_generator, _bad_generator, _syntax_error_generator,
                _crash_generator]:
        report = run_loop(_make_spec(max_iterations=1), generator_fn=gen,
                          generated_base_dir=tmp_path)
        assert isinstance(report, LoopReport)
