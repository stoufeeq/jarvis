"""Tests for MarketSession state detection — pure logic, no I/O."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.market_session import ET, MarketSession


def at(year, month, day, hour, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ET)


@pytest.mark.parametrize(
    "now_et,expected_state",
    [
        # Weekday Tuesday May 12, 2026 (not a holiday)
        (at(2026, 5, 12, 3, 0),  "closed_overnight"),
        (at(2026, 5, 12, 6, 0),  "pre_market"),
        (at(2026, 5, 12, 9, 0),  "pre_market"),
        (at(2026, 5, 12, 9, 30), "open"),
        (at(2026, 5, 12, 12, 0), "open"),
        (at(2026, 5, 12, 15, 59), "open"),
        (at(2026, 5, 12, 16, 0),  "after_hours"),
        (at(2026, 5, 12, 19, 30), "after_hours"),
        (at(2026, 5, 12, 20, 0),  "closed_overnight"),
        (at(2026, 5, 12, 23, 0),  "closed_overnight"),
        # Weekend
        (at(2026, 5, 9, 10, 0),  "closed_weekend"),   # Saturday
        (at(2026, 5, 10, 13, 0), "closed_weekend"),   # Sunday
        # Holiday — Memorial Day 2026 = May 25
        (at(2026, 5, 25, 11, 0), "closed_holiday"),
    ],
)
def test_session_state(now_et, expected_state):
    s = MarketSession(now_et.astimezone(ZoneInfo("UTC")))
    assert s.state == expected_state, f"At {now_et}: expected {expected_state}, got {s.state}"


def test_next_trading_day_skips_weekend():
    # Friday after close → next is Monday
    friday_4pm = at(2026, 5, 8, 16, 0).astimezone(ZoneInfo("UTC"))
    s = MarketSession(friday_4pm)
    nxt = s.next_trading_day()
    assert nxt.weekday() == 0, f"Expected Monday, got {nxt.strftime('%A')}"
    assert nxt.hour == 9 and nxt.minute == 30


def test_next_trading_day_skips_holiday():
    # Day before Memorial Day (May 25, 2026 is a Monday holiday)
    # Friday May 22 after close → next should be Tuesday May 26
    friday_before_memorial = at(2026, 5, 22, 17, 0).astimezone(ZoneInfo("UTC"))
    s = MarketSession(friday_before_memorial)
    nxt = s.next_trading_day()
    assert nxt.month == 5 and nxt.day == 26


def test_pre_market_returns_today_as_next_session():
    # Tuesday 8 AM ET — market opens at 9:30 same day
    pre_open = at(2026, 5, 12, 8, 0).astimezone(ZoneInfo("UTC"))
    s = MarketSession(pre_open)
    nxt = s.next_trading_day()
    assert nxt.day == 12 and nxt.hour == 9 and nxt.minute == 30


def test_describe_includes_human_readable():
    """Smoke test on the describe() output for each state."""
    for now in [at(2026, 5, 12, 12, 0), at(2026, 5, 9, 10, 0), at(2026, 5, 25, 11, 0)]:
        s = MarketSession(now.astimezone(ZoneInfo("UTC")))
        desc = s.describe()
        assert isinstance(desc, str) and len(desc) > 5
