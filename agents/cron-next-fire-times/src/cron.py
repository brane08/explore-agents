"""Deterministic 5-field cron expression parser and fire-time iterator (UTC).

Pure computation: no I/O, no network, no model access, no import-time side effects.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterator, List, Sequence, Set

MINUTE = 0
HOUR = 1
DOM = 2
MONTH = 3
DOW = 4

FIELD_NAMES = ("minute", "hour", "day-of-month", "month", "day-of-week")
FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))

MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
DOW_NAMES = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}

MACROS = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}

# Bounded search (§4.3): the sparsest legal schedule is Feb 29, whose consecutive
# occurrences are up to 8 years apart, so five of them span at most ~33 years. A
# 40-year horizon covers that with margin; day-level stepping keeps it cheap.
MAX_SEARCH_DAYS = 366 * 40


class CronError(ValueError):
    """Raised when an input cannot be parsed or a schedule never fires."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CronSchedule:
    """A parsed cron schedule, evaluated at minute granularity in UTC."""

    def __init__(self, expression: str) -> None:
        self.expression = expression
        (
            self.minutes,
            self.hours,
            self.days_of_month,
            self.months,
            self.days_of_week,
            self.dom_restricted,
            self.dow_restricted,
        ) = _parse(expression)

    def matches(self, moment: datetime) -> bool:
        if moment.month not in self.months:
            return False
        if not self._day_matches(moment):
            return False
        return moment.hour in self.hours and moment.minute in self.minutes

    def _day_matches(self, moment: datetime) -> bool:
        dom_hit = moment.day in self.days_of_month
        dow_hit = ((moment.weekday() + 1) % 7) in self.days_of_week
        if self.dom_restricted and self.dow_restricted:
            return dom_hit or dow_hit
        if self.dom_restricted:
            return dom_hit
        if self.dow_restricted:
            return dow_hit
        return True

    def iter_fire_times(self, start: datetime) -> Iterator[datetime]:
        """Yield fire times strictly after `start`, in ascending UTC order."""
        cursor = start.astimezone(timezone.utc).replace(second=0, microsecond=0)
        cursor += timedelta(minutes=1)
        day = cursor.replace(hour=0, minute=0)
        first_day = True
        for _ in range(MAX_SEARCH_DAYS):
            if day.month in self.months and self._day_matches(day):
                for hour in sorted(self.hours):
                    for minute in sorted(self.minutes):
                        moment = day.replace(hour=hour, minute=minute)
                        if first_day and moment < cursor:
                            continue
                        yield moment
            day += timedelta(days=1)
            first_day = False

    def next_fire_times(self, start: datetime, count: int) -> List[datetime]:
        if count < 0:
            raise CronError("INVALID_COUNT", "count must be >= 0")
        out: List[datetime] = []
        for moment in self.iter_fire_times(start):
            out.append(moment)
            if len(out) == count:
                return out
        raise CronError(
            "NO_FIRE_TIME",
            "expression '%s' produces fewer than %d fire times within %d days of the start"
            % (self.expression, count, MAX_SEARCH_DAYS),
        )


def _parse(expression: str):
    if not isinstance(expression, str):
        raise CronError("INVALID_CRON", "cron expression must be a string")
    expr = expression.strip()
    if not expr:
        raise CronError("INVALID_CRON", "cron expression is empty")
    if expr.startswith("@"):
        macro = MACROS.get(expr.lower())
        if macro is None:
            raise CronError("INVALID_CRON", "unsupported macro '%s'" % expr)
        expr = macro
    parts = expr.split()
    if len(parts) != 5:
        raise CronError(
            "INVALID_CRON",
            "expected 5 cron fields (minute hour day-of-month month day-of-week), got %d"
            % len(parts),
        )
    sets = [_parse_field(parts[i], i) for i in range(5)]
    dom_restricted = not _is_wildcard(parts[DOM])
    dow_restricted = not _is_wildcard(parts[DOW])
    days_of_week = {0 if v == 7 else v for v in sets[DOW]}
    return (
        sets[MINUTE],
        sets[HOUR],
        sets[DOM],
        sets[MONTH],
        days_of_week,
        dom_restricted,
        dow_restricted,
    )


def _is_wildcard(raw: str) -> bool:
    return raw.strip() in ("*", "?")


def _parse_field(raw: str, index: int) -> Set[int]:
    low, high = FIELD_RANGES[index]
    values: Set[int] = set()
    for term in raw.split(","):
        term = term.strip()
        if not term:
            raise CronError(
                "INVALID_CRON", "empty term in %s field '%s'" % (FIELD_NAMES[index], raw)
            )
        values |= _parse_term(term, index, low, high, raw)
    if not values:
        raise CronError(
            "INVALID_CRON", "%s field '%s' matches no value" % (FIELD_NAMES[index], raw)
        )
    return values


def _parse_term(term: str, index: int, low: int, high: int, raw: str) -> Set[int]:
    step = 1
    body = term
    if "/" in term:
        body, _, step_raw = term.partition("/")
        step = _to_int(step_raw, index, raw)
        if step <= 0:
            raise CronError(
                "INVALID_CRON", "step must be positive in %s field '%s'" % (FIELD_NAMES[index], raw)
            )
    if body in ("*", "?"):
        start, end = low, high
    elif "-" in body.lstrip("-"):
        start_raw, _, end_raw = body.partition("-")
        start = _to_named_int(start_raw, index, raw)
        end = _to_named_int(end_raw, index, raw)
    else:
        start = _to_named_int(body, index, raw)
        end = high if "/" in term else start
    for value in (start, end):
        if value < low or value > high:
            raise CronError(
                "INVALID_CRON",
                "value %d out of range %d-%d in %s field '%s'"
                % (value, low, high, FIELD_NAMES[index], raw),
            )
    if start > end:
        raise CronError(
            "INVALID_CRON",
            "range start %d exceeds end %d in %s field '%s'"
            % (start, end, FIELD_NAMES[index], raw),
        )
    return set(range(start, end + 1, step))


def _to_int(raw: str, index: int, field_raw: str) -> int:
    try:
        return int(raw.strip())
    except ValueError:
        raise CronError(
            "INVALID_CRON",
            "'%s' is not an integer in %s field '%s'" % (raw, FIELD_NAMES[index], field_raw),
        ) from None


def _to_named_int(raw: str, index: int, field_raw: str) -> int:
    token = raw.strip().lower()
    if index == MONTH and token in MONTH_NAMES:
        return MONTH_NAMES[token]
    if index == DOW and token in DOW_NAMES:
        return DOW_NAMES[token]
    if any(ch.isalpha() for ch in token):
        raise CronError(
            "INVALID_CRON",
            "unsupported token '%s' in %s field '%s' (L, W and # are not supported)"
            % (raw, FIELD_NAMES[index], field_raw),
        )
    return _to_int(token, index, field_raw)


def parse_timestamp(raw) -> datetime:
    """Parse an ISO-8601 timestamp (or datetime) into an aware UTC datetime."""
    if isinstance(raw, datetime):
        moment = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise CronError("INVALID_TIMESTAMP", "start timestamp is empty")
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            raise CronError(
                "INVALID_TIMESTAMP",
                "start timestamp '%s' is not a valid ISO-8601 timestamp" % raw,
            ) from None
    else:
        raise CronError(
            "INVALID_TIMESTAMP", "start timestamp must be an ISO-8601 string or datetime"
        )
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def format_utc(moments: Sequence[datetime]) -> List[str]:
    return [m.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") for m in moments]
