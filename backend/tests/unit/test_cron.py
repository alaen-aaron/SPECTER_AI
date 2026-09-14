"""Unit tests for the pure domain cron parser (M7.5 Phase 1 schedule safety)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.cron import (
    InvalidCronExpressionError,
    next_run,
    parse_cron_expression,
)


def _utc(*, year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def test_parse_valid_expression():
    cron = parse_cron_expression("0 0 * * *")
    assert cron.minutes == frozenset({0})
    assert cron.hours == frozenset({0})
    assert cron.dom_wildcard is True
    assert cron.dow_wildcard is True


def test_parse_star_step_and_lists():
    cron = parse_cron_expression("*/15 9-17 1,15 */2 0")
    assert cron.minutes == frozenset({0, 15, 30, 45})
    assert cron.hours == frozenset(range(9, 18))
    assert cron.days_of_month == frozenset({1, 15})
    assert cron.months == frozenset({1, 3, 5, 7, 9, 11})
    # dow: 0 = Sunday
    assert cron.days_of_week == frozenset({0})


def test_parse_dow_7_normalizes_to_sunday():
    cron = parse_cron_expression("0 0 * * 7")
    assert cron.days_of_week == frozenset({0})


def test_parse_requires_five_fields():
    with pytest.raises(InvalidCronExpressionError):
        parse_cron_expression("0 0 * *")
    with pytest.raises(InvalidCronExpressionError):
        parse_cron_expression("0 0 * * * *")


def test_parse_rejects_non_numeric_token():
    with pytest.raises(InvalidCronExpressionError):
        parse_cron_expression("abc 0 * * *")


def test_parse_rejects_out_of_range():
    with pytest.raises(InvalidCronExpressionError):
        parse_cron_expression("60 0 * * *")
    with pytest.raises(InvalidCronExpressionError):
        parse_cron_expression("0 24 * * *")
    with pytest.raises(InvalidCronExpressionError):
        parse_cron_expression("0 0 32 * *")
    with pytest.raises(InvalidCronExpressionError):
        parse_cron_expression("0 0 * 13 *")
    with pytest.raises(InvalidCronExpressionError):
        parse_cron_expression("0 0 * * 8")


def test_parse_rejects_zero_step():
    with pytest.raises(InvalidCronExpressionError):
        parse_cron_expression("*/0 * * * *")


def test_next_run_daily_at_midnight():
    cron = parse_cron_expression("0 0 * * *")
    nxt = next_run(_utc(year=2026, month=9, day=9, hour=10, minute=30), cron)
    assert nxt == _utc(year=2026, month=9, day=10, hour=0, minute=0)


def test_next_run_is_strictly_after():
    cron = parse_cron_expression("0 0 * * *")
    nxt = next_run(_utc(year=2026, month=9, day=10, hour=0, minute=0), cron)
    assert nxt == _utc(year=2026, month=9, day=11, hour=0, minute=0)


def test_next_run_every_15_minutes():
    cron = parse_cron_expression("*/15 * * * *")
    nxt = next_run(_utc(year=2026, month=9, day=9, hour=10, minute=30), cron)
    assert nxt == _utc(year=2026, month=9, day=9, hour=10, minute=45)


def test_next_run_nine_to_five_weekdays():
    cron = parse_cron_expression("0 9-17 * * 1-5")
    nxt = next_run(_utc(year=2026, month=9, day=11, hour=18, minute=0), cron)
    # Friday 18:00 -> next weekday match is Monday 09:00.
    assert nxt == _utc(year=2026, month=9, day=14, hour=9, minute=0)


def test_next_run_dom_dow_or_rule():
    # When both dom and dow are restricted, cron fires if EITHER matches.
    cron = parse_cron_expression("0 0 13 * 5")
    # 2026-09-04 is a Friday (dow side matches; dom 13 does not).
    nxt = next_run(_utc(year=2026, month=9, day=1, hour=0, minute=0), cron)
    assert nxt == _utc(year=2026, month=9, day=4, hour=0, minute=0)
    # 2026-11-13 is the next Friday the 13th (BOTH sides match).
    nxt2 = next_run(_utc(year=2026, month=11, day=9, hour=0, minute=0), cron)
    assert nxt2 == _utc(year=2026, month=11, day=13, hour=0, minute=0)


def test_next_run_month_restriction_rolls_year():
    cron = parse_cron_expression("0 0 1 1 *")
    nxt = next_run(_utc(year=2026, month=9, day=9, hour=0, minute=0), cron)
    assert nxt == _utc(year=2027, month=1, day=1, hour=0, minute=0)


def test_next_run_never_fires_feb_30():
    cron = parse_cron_expression("0 0 30 2 *")
    assert next_run(_utc(year=2026, month=1, day=1, hour=0, minute=0), cron) is None


def test_next_run_respects_aware_dt_timezone():
    cron = parse_cron_expression("0 12 * * *")
    nxt = next_run(_utc(year=2026, month=9, day=9, hour=10, minute=0), cron)
    assert nxt == _utc(year=2026, month=9, day=9, hour=12, minute=0)
    assert nxt.tzinfo is not None
