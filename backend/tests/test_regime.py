"""Tests for the regime classifier.

The classify() function is a pure decision tree — no I/O, no state —
so it's easy to nail down the six regime cells and the two VIX
thresholds (20 for high_vol, 30 for crisis). The service layer
(fetch + upsert) is exercised by integration testing in production;
unit-testing it would mostly mock yfinance.
"""

import pytest

from app.services.regime import VIX_HIGH_THRESHOLD, VIX_CRISIS_THRESHOLD, classify


class TestClassify:
    def test_bull_low_vol(self):
        assert classify(spx_close=5000, spx_sma200=4800, vix_close=15) == "bull_low_vol"

    def test_bull_high_vol(self):
        assert classify(spx_close=5000, spx_sma200=4800, vix_close=25) == "bull_high_vol"

    def test_bull_crisis(self):
        assert classify(spx_close=5000, spx_sma200=4800, vix_close=35) == "bull_crisis"

    def test_bear_low_vol(self):
        assert classify(spx_close=4500, spx_sma200=4800, vix_close=15) == "bear_low_vol"

    def test_bear_high_vol(self):
        assert classify(spx_close=4500, spx_sma200=4800, vix_close=25) == "bear_high_vol"

    def test_bear_crisis(self):
        assert classify(spx_close=4500, spx_sma200=4800, vix_close=35) == "bear_crisis"

    def test_vix_at_high_threshold_counts_as_high(self):
        """VIX exactly at 20 → high_vol (inclusive on the higher side —
        any spike to the boundary gets the more defensive classification)."""
        assert classify(spx_close=5000, spx_sma200=4800, vix_close=VIX_HIGH_THRESHOLD) == "bull_high_vol"

    def test_vix_at_crisis_threshold_counts_as_crisis(self):
        """VIX exactly at 30 → crisis. Same inclusive-on-higher-side rule."""
        assert classify(spx_close=5000, spx_sma200=4800, vix_close=VIX_CRISIS_THRESHOLD) == "bull_crisis"

    def test_spx_exactly_at_sma_counts_as_bear(self):
        """SPX > SMA200 is bull; == is bear. Ties break bearish (defensive
        default: only unambiguous uptrends get the bullish flag)."""
        assert classify(spx_close=4800, spx_sma200=4800, vix_close=15) == "bear_low_vol"


@pytest.mark.parametrize("spx,sma,vix,expected", [
    # bull tiers
    (5100, 4800, 12, "bull_low_vol"),
    (5100, 4800, 22, "bull_high_vol"),
    (5100, 4800, 45, "bull_crisis"),
    # bear tiers
    (4200, 4800, 15, "bear_low_vol"),
    (4200, 4800, 25, "bear_high_vol"),
    (4200, 4800, 40, "bear_crisis"),
])
def test_classify_matrix(spx, sma, vix, expected):
    assert classify(spx, sma, vix) == expected
