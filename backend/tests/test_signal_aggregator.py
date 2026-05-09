"""Tests for SignalAggregator — sum-of-strengths math + grouping logic."""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from app.models.signal import Signal, SignalDirection, SignalType
from app.services.signal_aggregator import SignalAggregator, _aggregate_one


def make_signal(
    ticker: str,
    signal_type: SignalType,
    direction: SignalDirection,
    strength: int,
) -> Signal:
    return Signal(
        ticker=ticker,
        signal_type=signal_type,
        direction=direction,
        strength=strength,
        rationale=f"test {direction.value}",
        created_at=datetime.now(UTC),
    )


def test_aggregate_one_unanimous_bullish():
    rules = [
        make_signal("AAPL", SignalType.fundamental, SignalDirection.bullish, 4),
        make_signal("AAPL", SignalType.fundamental, SignalDirection.bullish, 3),
        make_signal("AAPL", SignalType.fundamental, SignalDirection.bullish, 5),
    ]
    agg = _aggregate_one(rules)
    assert agg["net_direction"] == "bullish"
    assert agg["confidence"] == "strong"  # all agree
    assert agg["score"] == 12
    assert agg["bullish_count"] == 3
    assert agg["bearish_count"] == 0


def test_aggregate_one_mixed():
    rules = [
        make_signal("AAPL", SignalType.fundamental, SignalDirection.bullish, 3),
        make_signal("AAPL", SignalType.fundamental, SignalDirection.bullish, 4),
        make_signal("AAPL", SignalType.fundamental, SignalDirection.bearish, 3),
        make_signal("AAPL", SignalType.fundamental, SignalDirection.bearish, 2),
    ]
    agg = _aggregate_one(rules)
    # Score = +3 + 4 - 3 - 2 = +2 → bullish but barely
    assert agg["score"] == 2
    assert agg["net_direction"] == "bullish"
    # 2/4 bullish = 50%, not unanimous, not >=70% → mixed
    assert agg["confidence"] == "mixed"
    assert agg["bullish_count"] == 2
    assert agg["bearish_count"] == 2


def test_aggregate_one_dead_tie_is_neutral():
    rules = [
        make_signal("AAPL", SignalType.fundamental, SignalDirection.bullish, 3),
        make_signal("AAPL", SignalType.fundamental, SignalDirection.bearish, 3),
    ]
    agg = _aggregate_one(rules)
    assert agg["score"] == 0
    assert agg["net_direction"] == "neutral"


def test_aggregate_moderate_confidence():
    """3 of 4 agree → moderate (>=70%) but not strong."""
    rules = [
        make_signal("AAPL", SignalType.fundamental, SignalDirection.bullish, 3),
        make_signal("AAPL", SignalType.fundamental, SignalDirection.bullish, 3),
        make_signal("AAPL", SignalType.fundamental, SignalDirection.bullish, 3),
        make_signal("AAPL", SignalType.fundamental, SignalDirection.bearish, 1),
    ]
    agg = _aggregate_one(rules)
    assert agg["net_direction"] == "bullish"
    # 3/4 = 75% bullish agreement → moderate
    assert agg["confidence"] == "moderate"


@pytest_asyncio.fixture
async def populated_db(db):
    """Insert a mix of signals across tickers and categories."""
    for ticker in ("AAPL", "NVDA"):
        for direction in (SignalDirection.bullish, SignalDirection.bearish):
            db.add(make_signal(ticker, SignalType.technical, direction, 4))
            db.add(make_signal(ticker, SignalType.fundamental, direction, 3))
    await db.commit()
    return db


@pytest.mark.asyncio
async def test_aggregator_groups_by_ticker_and_category(populated_db):
    aggs = await SignalAggregator(populated_db).aggregated_by_ticker_category(limit=100)

    # 2 tickers × 2 categories = 4 entries (each with one bullish + one bearish rule)
    by_key = {(a["ticker"], a["signal_type"]): a for a in aggs}
    assert len(by_key) == 4

    # Each pair has 1 bullish + 1 bearish — net should be (4-3)=+1 for technical, etc.
    for key, agg in by_key.items():
        assert agg["rule_count"] == 2
        assert agg["bullish_count"] == 1
        assert agg["bearish_count"] == 1
