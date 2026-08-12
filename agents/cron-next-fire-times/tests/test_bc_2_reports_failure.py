"""BC-2: When the task cannot be completed, the agent reports the failure instead of
returning a partial or fabricated result."""

import pytest

from agent import run
from cron import CronError, CronSchedule


@pytest.mark.parametrize(
    "cron_expression, start_timestamp, expected_code",
    [
        ("* * * *", "2026-08-12T10:00:00Z", "INVALID_CRON"),
        ("*/0 * * * *", "2026-08-12T10:00:00Z", "INVALID_CRON"),
        ("99 * * * *", "2026-08-12T10:00:00Z", "INVALID_CRON"),
        ("0 0 L * *", "2026-08-12T10:00:00Z", "INVALID_CRON"),
        ("0 0 * * MON#2", "2026-08-12T10:00:00Z", "INVALID_CRON"),
        ("30-10 * * * *", "2026-08-12T10:00:00Z", "INVALID_CRON"),
        ("@never", "2026-08-12T10:00:00Z", "INVALID_CRON"),
        ("", "2026-08-12T10:00:00Z", "INVALID_CRON"),
        ("* * * * *", "not-a-timestamp", "INVALID_TIMESTAMP"),
        ("* * * * *", "2026-13-45T99:00:00Z", "INVALID_TIMESTAMP"),
        ("* * * * *", 1755000000, "INVALID_TIMESTAMP"),
        ("* * * * *", None, "MISSING_INPUT"),
        (None, "2026-08-12T10:00:00Z", "MISSING_INPUT"),
    ],
)
def test_bc_2_reports_failure_without_partial_or_fabricated_result(
    cron_expression, start_timestamp, expected_code
):
    result = run(cron_expression, start_timestamp)

    assert result["status"] == "error"
    assert result["error"]["code"] == expected_code
    assert result["error"]["message"]
    # No partial or fabricated schedule is returned alongside the failure.
    assert "fire_times" not in result


def test_bc_2_unsatisfiable_schedule_is_reported_not_padded():
    # Feb 30 never occurs: fewer than five fire times exist, so this must fail
    # rather than return a short or invented list.
    result = run("0 0 30 2 *", "2026-01-01T00:00:00Z")
    assert result["status"] == "error"
    assert result["error"]["code"] == "NO_FIRE_TIME"
    assert "fire_times" not in result


def test_bc_2_failure_is_typed_at_the_computation_layer_too():
    from datetime import datetime, timezone

    with pytest.raises(CronError) as excinfo:
        CronSchedule("0 0 30 2 *").next_fire_times(
            datetime(2026, 1, 1, tzinfo=timezone.utc), 5
        )
    assert excinfo.value.code == "NO_FIRE_TIME"

    with pytest.raises(CronError) as parse_error:
        CronSchedule("not a cron")
    assert parse_error.value.code == "INVALID_CRON"
