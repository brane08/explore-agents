#!/usr/bin/env python3
"""Standalone criterion eval runner (no pytest, no arguments).

Writes a JSON report to ${EVAL_REPORT} if set, otherwise to stdout.
Exit code 0 only when every criterion passed.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.join(ROOT, "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from agent import run  # noqa: E402
from config import AgentConfig  # noqa: E402
from cron import CronSchedule  # noqa: E402

MODEL_PROFILE = os.environ.get("MODEL_PROFILE", "stub-class-ref")


def _parse(stamp):
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def check_bc_1():
    """Next five fire times in UTC for a cron expression + start timestamp."""
    cases = [
        (
            "*/15 * * * *",
            "2026-08-12T10:07:00Z",
            [
                "2026-08-12T10:15:00Z",
                "2026-08-12T10:30:00Z",
                "2026-08-12T10:45:00Z",
                "2026-08-12T11:00:00Z",
                "2026-08-12T11:15:00Z",
            ],
        ),
        (
            "30 2 * * *",
            "2026-08-30T03:00:00Z",
            [
                "2026-08-31T02:30:00Z",
                "2026-09-01T02:30:00Z",
                "2026-09-02T02:30:00Z",
                "2026-09-03T02:30:00Z",
                "2026-09-04T02:30:00Z",
            ],
        ),
        (
            "0 9 * * MON",
            "2026-08-12T00:00:00Z",
            [
                "2026-08-17T09:00:00Z",
                "2026-08-24T09:00:00Z",
                "2026-08-31T09:00:00Z",
                "2026-09-07T09:00:00Z",
                "2026-09-14T09:00:00Z",
            ],
        ),
        (
            "0 0 29 2 *",
            "2026-01-01T00:00:00Z",
            [
                "2028-02-29T00:00:00Z",
                "2032-02-29T00:00:00Z",
                "2036-02-29T00:00:00Z",
                "2040-02-29T00:00:00Z",
                "2044-02-29T00:00:00Z",
            ],
        ),
    ]
    notes = []
    ok = AgentConfig().fire_time_count == 5
    if not ok:
        notes.append("default fire_time_count != 5")
    for expression, start, expected in cases:
        result = run(expression, start)
        if result.get("status") != "ok":
            ok = False
            notes.append("%s: status=%s" % (expression, result.get("status")))
            continue
        if result.get("timezone") != "UTC":
            ok = False
            notes.append("%s: timezone=%s" % (expression, result.get("timezone")))
        if result.get("fire_times") != expected:
            ok = False
            notes.append("%s: got %s" % (expression, result.get("fire_times")))

    # Offset-bearing start normalises to UTC.
    offset = run("0 * * * *", "2026-08-12T10:07:00+02:00")
    if offset.get("start_timestamp") != "2026-08-12T08:07:00Z":
        ok = False
        notes.append("offset start not normalised: %s" % offset.get("start_timestamp"))

    # Brute-force cross-check: no matching minute skipped.
    expression = "5,35 1-4 * * *"
    start = _parse("2026-03-08T00:00:00Z")
    schedule = CronSchedule(expression)
    cursor, expected = start + timedelta(minutes=1), []
    while len(expected) < 5:
        if schedule.matches(cursor):
            expected.append(cursor.strftime("%Y-%m-%dT%H:%M:%SZ"))
        cursor += timedelta(minutes=1)
    brute = run(expression, "2026-03-08T00:00:00Z")
    if brute.get("fire_times") != expected:
        ok = False
        notes.append("brute-force mismatch: %s vs %s" % (brute.get("fire_times"), expected))
    return ok, notes


def check_bc_2():
    """Failures are reported, never partial or fabricated."""
    cases = [
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
        ("0 0 30 2 *", "2026-01-01T00:00:00Z", "NO_FIRE_TIME"),
    ]
    ok, notes = True, []
    for expression, start, code in cases:
        result = run(expression, start)
        if result.get("status") != "error":
            ok = False
            notes.append("%r/%r: expected error, got %s" % (expression, start, result))
            continue
        if result.get("error", {}).get("code") != code:
            ok = False
            notes.append(
                "%r/%r: expected %s, got %s" % (expression, start, code, result["error"])
            )
        if "fire_times" in result:
            ok = False
            notes.append("%r/%r: partial result leaked" % (expression, start))
    return ok, notes


CHECKS = [("BC-1", check_bc_1), ("BC-2", check_bc_2)]


def main():
    criteria = []
    all_passed = True
    for criterion_id, check in CHECKS:
        try:
            passed, notes = check()
        except Exception as exc:  # noqa: BLE001 - eval must not crash silently
            passed, notes = False, ["unhandled exception: %r" % exc]
        all_passed = all_passed and passed
        entry = {"id": criterion_id, "passed": bool(passed)}
        if notes:
            entry["notes"] = notes
        criteria.append(entry)

    report = {"model_profile": MODEL_PROFILE, "criteria": criteria}
    payload = json.dumps(report, indent=2)
    target = os.environ.get("EVAL_REPORT")
    if target:
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    else:
        sys.stdout.write(payload + "\n")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
