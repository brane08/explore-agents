"""
Unit tests for the B2b reasoning harness (no MCP, no model).

The load-bearing test is the leak guard: the generator's prompt must NOT
contain the task's canonical tool call, or a "passing" agent would just be
copying the answer rather than inferring it.
"""
from __future__ import annotations

import ast
import json

import pytest

from eval_suite.reasoning_tasks import ANSWERABLE_TASKS, GAP_TASKS, HELDOUT_TASKS, TRAIN_DEMOS
from loop.reasoning import _WRONG, build_reasoning_prompt, grade, render_answer_agent
from loop.spec import ToolSchema

_CATALOG = [
    ToolSchema(name="es_search", description="Search logs by level", input_schema={"type": "object"}),
    ToolSchema(name="es_aggregate", description="Aggregate by field", input_schema={"type": "object"}),
    ToolSchema(name="field_stats", description="Field statistics", input_schema={"type": "object"}),
]


@pytest.mark.parametrize("task", HELDOUT_TASKS, ids=lambda t: t.id)
def test_prompt_hides_canonical_call(task):
    prompt = build_reasoning_prompt(task, _CATALOG)
    assert task.prompt in prompt                    # the request is shown
    if task.answerable:
        # the canonical answer (tool args) is NOT handed to the generator
        assert json.dumps(task.arguments) not in prompt
    else:
        # nothing marks this task as a gap — the prompt must read identically
        # to an answerable task's prompt apart from the request itself
        assert task.id not in prompt


def test_prompt_includes_catalog_and_demos():
    prompt = build_reasoning_prompt(HELDOUT_TASKS[0], _CATALOG)
    assert "es_search" in prompt and "field_stats" in prompt
    assert TRAIN_DEMOS[0].prompt in prompt          # few-shot examples present


def test_train_and_heldout_are_disjoint():
    train_prompts = {d.prompt for d in TRAIN_DEMOS}
    held_prompts = {t.prompt for t in HELDOUT_TASKS}
    assert train_prompts.isdisjoint(held_prompts)
    # no held-out canonical call leaks through the demo set either
    demo_blob = json.dumps([[d.tool_name, d.arguments] for d in TRAIN_DEMOS])
    for t in ANSWERABLE_TASKS:
        assert json.dumps(t.arguments) not in demo_blob


def test_render_answer_agent_is_valid_python():
    src = render_answer_agent("es_search", {"query_level": "ERROR", "size": 10}, "http://x")
    ast.parse(src)
    assert '"answer"' in src
    assert "except Exception" in src                # resilient, like B2a nodes


# --- gap detection (unit-gradeable: these paths never touch MCP) ---

def test_suite_contains_gap_tasks():
    assert len(GAP_TASKS) >= 3
    assert len(ANSWERABLE_TASKS) >= 6


def test_prompt_teaches_missing_format():
    prompt = build_reasoning_prompt(HELDOUT_TASKS[0], _CATALOG)
    assert '"missing"' in prompt                    # gap demo + instruction present


def test_grade_accepts_correct_gap_report():
    task = GAP_TASKS[0]  # delete_old_errors
    out = json.dumps({"missing": "No tool can delete or modify log records."})
    passed, _, detail, missing = grade(task, out, "http://unused")
    assert passed and detail == ""
    assert "delete" in missing.lower()


def test_grade_rejects_fabricated_answer_on_gap_task():
    task = GAP_TASKS[0]
    out = json.dumps({"answer": {"hits": [], "total": 0}})
    passed, _, detail, _ = grade(task, out, "http://unused")
    assert not passed
    assert detail == _WRONG                         # symmetric — no giveaway


def test_grade_rejects_offtopic_gap_report():
    task = GAP_TASKS[0]  # needs a delete-ish description
    out = json.dumps({"missing": "The weather service is unavailable."})
    passed, _, detail, _ = grade(task, out, "http://unused")
    assert not passed and detail == _WRONG


def test_grade_rejects_gap_claim_on_answerable_task():
    task = ANSWERABLE_TASKS[0]
    out = json.dumps({"missing": "no tool can search logs"})
    passed, _, detail, missing = grade(task, out, "http://unused")
    assert not passed
    assert detail == _WRONG                         # same message as any wrong answer
    assert missing is not None                      # but the claim is recorded


def test_symmetric_detail_never_reveals_task_kind():
    gap = grade(GAP_TASKS[0], json.dumps({"answer": {}}), "http://unused")[2]
    ans = grade(ANSWERABLE_TASKS[0], json.dumps({"missing": "x tool"}), "http://unused")[2]
    assert gap == ans == _WRONG
    for word in ("gap", "unanswerable", "missing"):
        assert word not in _WRONG.lower()


def test_feedback_appears_in_retry_prompt():
    task = HELDOUT_TASKS[0]
    first = build_reasoning_prompt(task, _CATALOG)
    retry = build_reasoning_prompt(task, _CATALOG, feedback="answer did not match")
    assert "previous attempt" not in first.lower()
    assert "answer did not match" in retry
    # feedback must not smuggle in the canonical call either
    assert json.dumps(task.arguments) not in retry
