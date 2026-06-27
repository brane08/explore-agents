import pytest
from log_mock.generator import generate_logs

def test_generate_returns_count():
    logs = generate_logs(count=10)
    assert len(logs) == 10

def test_log_record_has_required_fields():
    logs = generate_logs(count=1)
    record = logs[0]
    for field in ("timestamp", "level", "service", "message", "duration_ms", "status_code"):
        assert field in record, f"missing field: {field}"

def test_level_values_are_valid():
    logs = generate_logs(count=50, seed=42)
    valid_levels = {"INFO", "WARN", "ERROR", "DEBUG"}
    assert all(r["level"] in valid_levels for r in logs)

def test_seed_is_deterministic():
    a = generate_logs(count=5, seed=1)
    b = generate_logs(count=5, seed=1)
    assert a == b

def test_different_seeds_differ():
    a = generate_logs(count=5, seed=1)
    b = generate_logs(count=5, seed=2)
    assert a != b
