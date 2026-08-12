"""BC-1: Given a cron expression and a start timestamp, return the next five fire
times in UTC."""

from datetime import datetime, timedelta, timezone

from agent import run
from config import AgentConfig
from cron import CronSchedule


def test_bc_1_returns_next_five_fire_times_in_utc():
    result = run("*/15 * * * *", "2026-08-12T10:07:00Z")

    assert result["status"] == "ok"
    assert result["timezone"] == "UTC"
    assert result["fire_times"] == [
        "2026-08-12T10:15:00Z",
        "2026-08-12T10:30:00Z",
        "2026-08-12T10:45:00Z",
        "2026-08-12T11:00:00Z",
        "2026-08-12T11:15:00Z",
    ]
    assert len(result["fire_times"]) == 5

    # Daily schedule crossing a month boundary.
    daily = run("30 2 * * *", "2026-08-30T03:00:00Z")
    assert daily["fire_times"] == [
        "2026-08-31T02:30:00Z",
        "2026-09-01T02:30:00Z",
        "2026-09-02T02:30:00Z",
        "2026-09-03T02:30:00Z",
        "2026-09-04T02:30:00Z",
    ]

    # Day-of-week schedule (Mondays 09:00).
    weekly = run("0 9 * * MON", "2026-08-12T00:00:00Z")
    assert weekly["fire_times"] == [
        "2026-08-17T09:00:00Z",
        "2026-08-24T09:00:00Z",
        "2026-08-31T09:00:00Z",
        "2026-09-07T09:00:00Z",
        "2026-09-14T09:00:00Z",
    ]

    # Sparse schedule spanning years (Feb 29 -> leap years only).
    leap = run("0 0 29 2 *", "2026-01-01T00:00:00Z")
    assert leap["fire_times"] == [
        "2028-02-29T00:00:00Z",
        "2032-02-29T00:00:00Z",
        "2036-02-29T00:00:00Z",
        "2040-02-29T00:00:00Z",
        "2044-02-29T00:00:00Z",
    ]


def test_bc_1_non_utc_start_is_normalised_and_five_is_the_default():
    result = run("0 * * * *", "2026-08-12T10:07:00+02:00")
    assert result["start_timestamp"] == "2026-08-12T08:07:00Z"
    assert result["fire_times"][0] == "2026-08-12T09:00:00Z"
    assert len(result["fire_times"]) == 5
    assert AgentConfig().fire_time_count == 5


def test_bc_1_fire_times_are_strictly_after_start_ascending_and_match_the_schedule():
    start_raw = "2026-03-08T00:00:00Z"
    result = run("5,35 1-4 * * *", start_raw)
    start = datetime(2026, 3, 8, tzinfo=timezone.utc)
    schedule = CronSchedule("5,35 1-4 * * *")

    moments = [
        datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        for t in result["fire_times"]
    ]
    assert moments == sorted(moments)
    assert all(m > start for m in moments)
    assert all(schedule.matches(m) for m in moments)

    # No matching minute is skipped between consecutive fire times.
    cursor = start + timedelta(minutes=1)
    expected = []
    while len(expected) < 5:
        if schedule.matches(cursor):
            expected.append(cursor)
        cursor += timedelta(minutes=1)
    assert moments == expected
