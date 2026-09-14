"""Minimal 5-field cron expression support (M7.5 Phase 1 schedule safety).

Pure Python and framework-free on purpose: `application/` depends on
`domain/`, so schedule-enforcement logic — a safety-critical boundary —
lives here with zero knowledge of SQLAlchemy, FastAPI, or Celery.

Implements the de-facto Vixie-cron semantics for the classic five fields:

    minute       0-59
    hour         0-23
    day-of-month 1-31
    month        1-12
    day-of-week  0-7   (0 and 7 both mean Sunday)

Supported tokens per field: ``*``, ``*/n`` (step), ``a-b`` (inclusive
range), ``a-b/n`` (stepped range), and ``a,b,c`` (list).

Day-of-week/day-of-month matching follows the traditional cron OR rule:
when BOTH are restricted the match succeeds if EITHER field matches;
when one is ``*``, both fields must agree. This is the single most common
source of "the schedule fired on the wrong day" bugs in hand-rolled
implementations, so it is deliberately covered by unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

_FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))

# How far ahead `next_run` is willing to search. A schedule whose
# expression cannot produce a match inside this window is treated as
# "never fires" (ScheduleService disables it) rather than looping forever.
_MAX_SEARCH_DAYS = 365 * 5


class InvalidCronExpressionError(ValueError):
    """Raised when a 5-field cron expression cannot be parsed or is invalid."""


@dataclass(frozen=True, slots=True)
class CronExpression:
    """A parsed 5-field cron schedule."""

    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months: frozenset[int]
    days_of_week: frozenset[int]
    dom_wildcard: bool
    dow_wildcard: bool

    def _day_matches(self, candidate: datetime) -> bool:
        dom_matches = candidate.day in self.days_of_month
        # Python weekday(): Monday=0 .. Sunday=6. Cron Sunday=0, so rotate.
        cron_dow = (candidate.weekday() + 1) % 7
        dow_matches = cron_dow in self.days_of_week

        if self.dom_wildcard and self.dow_wildcard:
            return True
        if self.dom_wildcard:
            return dow_matches
        if self.dow_wildcard:
            return dom_matches
        # Both restricted: classic OR rule.
        return dom_matches or dow_matches


def _int_or_raise(token: str, field: str) -> int:
    try:
        return int(token)
    except ValueError as exc:
        raise InvalidCronExpressionError(
            f"Non-numeric token '{token}' in field '{field}'."
        ) from exc


def _parse_field(field: str, lo: int, hi: int, label: str) -> frozenset[int]:
    if field == "*":
        return frozenset(range(lo, hi + 1))

    if field.startswith("*/"):
        step = _int_or_raise(field[2:], label)
        if step <= 0:
            raise InvalidCronExpressionError(f"Step must be >= 1 in '{field}'.")
        return frozenset(range(lo, hi + 1, step))

    values: set[int] = set()
    for part in field.split(","):
        if not part:
            raise InvalidCronExpressionError(f"Empty list item in '{field}'.")
        if "/" in part:
            base, step_token = part.split("/", 1)
            step = _int_or_raise(step_token, label)
            if step <= 0:
                raise InvalidCronExpressionError(f"Step must be >= 1 in '{part}'.")
            if base == "*":
                values.update(range(lo, hi + 1, step))
                continue
            if "-" in base:
                start, end = base.split("-", 1)
            else:
                start = end = base
            s = _int_or_raise(start, label)
            e = _int_or_raise(end, label)
            _check_value(s, lo, hi, label)
            _check_value(e, lo, hi, label)
            values.update(range(s, e + 1, step))
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            s = _int_or_raise(start, label)
            e = _int_or_raise(end, label)
            _check_value(s, lo, hi, label)
            _check_value(e, lo, hi, label)
            values.update(range(s, e + 1))
            continue
        value = _int_or_raise(part, label)
        _check_value(value, lo, hi, label)
        values.add(value)
    return frozenset(values)


def _check_value(value: int, lo: int, hi: int, label: str) -> None:
    if not lo <= value <= hi:
        raise InvalidCronExpressionError(
            f"Value {value} out of range for '{label}' (expected {lo}-{hi})."
        )


def parse_cron_expression(expression: str) -> CronExpression:
    """Parse a 5-field cron expression into a `CronExpression`."""
    fields = expression.strip().split()
    if len(fields) != 5:
        raise InvalidCronExpressionError(
            f"Cron expression must have exactly 5 fields, got {len(fields)}: " f"'{expression}'."
        )

    minute_f, hour_f, dom_f, month_f, dow_f = fields

    minutes = _parse_field(minute_f, 0, 59, "minute")
    hours = _parse_field(hour_f, 0, 23, "hour")
    days_of_month = _parse_field(dom_f, 1, 31, "day-of-month")
    months = _parse_field(month_f, 1, 12, "month")
    dow_raw = _parse_field(dow_f, 0, 7, "day-of-week")
    days_of_week = frozenset(0 if day == 7 else day for day in dow_raw)

    return CronExpression(
        minutes=minutes,
        hours=hours,
        days_of_month=days_of_month,
        months=months,
        days_of_week=days_of_week,
        dom_wildcard=dom_f == "*",
        dow_wildcard=dow_f == "*",
    )


def _next_hour_on_day(candidate: datetime, hours: frozenset[int]) -> datetime | None:
    """Return the next candidate strictly inside the same day, or None."""
    for hour in sorted(hours):
        if hour > candidate.hour:
            return candidate.replace(hour=hour, minute=0)
    return None


def _next_month_start(candidate: datetime) -> datetime:
    first_of_month = candidate.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (first_of_month + timedelta(days=32)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )


def next_run(after: datetime, cron: CronExpression) -> datetime | None:
    """Next datetime strictly after `after` matching `cron` (UTC naive/aware-safe).

    Returns ``None`` when the expression cannot match within five years
    (for example ``0 0 30 2 *`` — Feb 30 never exists).
    """
    # Callers pass UTC-aware datetimes; naive datetimes are kept as-is.
    anchor = after

    first_tick = anchor.replace(second=0, microsecond=0) + timedelta(minutes=1)
    if first_tick <= anchor:
        return None
    candidate = first_tick
    horizon = anchor + timedelta(days=_MAX_SEARCH_DAYS)

    while candidate <= horizon:
        if candidate.month not in cron.months:
            candidate = _next_month_start(candidate)
            continue

        if not cron._day_matches(candidate):  # noqa: SLF001 - internal helper
            candidate = candidate.replace(hour=0, minute=0) + timedelta(days=1)
            continue

        if candidate.hour not in cron.hours:
            next_hour = _next_hour_on_day(candidate, cron.hours)
            if next_hour is None:
                candidate = candidate.replace(hour=0, minute=0) + timedelta(days=1)
            else:
                candidate = next_hour
            continue

        if candidate.minute not in cron.minutes:
            candidate = candidate + timedelta(minutes=1)
            continue

        return candidate

    return None
