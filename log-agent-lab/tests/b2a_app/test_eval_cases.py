"""
Verifies that the eval cases are correct when called directly against mcp_server.
Requires mcp_server running at MCP_SERVER_URL (default http://localhost:8000).

These tests establish ground truth before any generated agent exists.
"""

import os
import pytest
from eval_suite.log_analysis_cases import (
    CASES,
    CaseResult,
    _all_hits_are_error,
    _has_categorical_stats,
    _has_numeric_stats,
    _has_service_buckets,
    run_cases,
)

MCP_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:8000")


# --- unit tests for check functions (no network) ---

def test_check_all_hits_are_error_passes():
    assert _all_hits_are_error({"hits": [{"level": "ERROR"}, {"level": "ERROR"}]})


def test_check_all_hits_are_error_fails_on_mixed():
    assert not _all_hits_are_error({"hits": [{"level": "ERROR"}, {"level": "INFO"}]})


def test_check_all_hits_are_error_fails_on_empty():
    assert not _all_hits_are_error({"hits": []})


def test_check_has_service_buckets_passes():
    assert _has_service_buckets({"buckets": [{"key": "svc-a", "count": 5}]})


def test_check_has_service_buckets_fails_on_empty():
    assert not _has_service_buckets({"buckets": []})


def test_check_has_service_buckets_fails_on_zero_count():
    assert not _has_service_buckets({"buckets": [{"key": "svc-a", "count": 0}]})


def test_check_has_numeric_stats_passes():
    assert _has_numeric_stats({"min": 1.0, "max": 5.0, "avg": 3.0, "count": 10})


def test_check_has_numeric_stats_fails_on_missing_key():
    assert not _has_numeric_stats({"min": 1.0, "max": 5.0, "avg": 3.0})


def test_check_has_categorical_stats_passes():
    assert _has_categorical_stats({"cardinality": 4, "top_values": []})


def test_check_has_categorical_stats_fails_on_zero():
    assert not _has_categorical_stats({"cardinality": 0})


# --- all cases registered ---

def test_all_case_ids_are_unique():
    ids = [c.id for c in CASES]
    assert len(ids) == len(set(ids))


def test_four_cases_defined():
    assert len(CASES) == 4


# --- network tests: cases must pass against real mcp_server ---

@pytest.mark.anyio
async def test_run_cases_all_pass_against_mcp_server():
    results = await run_cases(MCP_URL)
    failures = [r for r in results if not r.passed]
    assert failures == [], f"Cases failed: {[(r.case_id, r.error) for r in failures]}"


@pytest.mark.anyio
async def test_run_cases_subset_by_id():
    results = await run_cases(MCP_URL, case_ids=["search_error_level", "field_stats_numeric"])
    assert len(results) == 2
    assert all(r.passed for r in results)


@pytest.mark.anyio
async def test_check_functions_correctly_reject_wrong_data():
    """Deliberately pass wrong data to check functions to confirm they can fail."""
    results = await run_cases(MCP_URL, case_ids=["search_error_level"])
    # swap in wrong data — the check must reject it
    wrong = CaseResult(case_id="search_error_level", passed=False,
                       result={"hits": [{"level": "INFO"}]})
    assert not _all_hits_are_error(wrong.result)
