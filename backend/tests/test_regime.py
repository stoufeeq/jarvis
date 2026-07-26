"""Tests for the regime classifier.

The classify() function is a pure decision tree — no I/O, no state —
so it's easy to nail down the four regime cells and the exact thresholds.
The service layer (fetch + upsert) is exercised by integration testing
in production; unit-testing it would mostly mock yfinance.
"""

import pytest

from app.services.regime import VIX_THRESHOLD, classify


class TestClassify:
    def test_bull_low_vol(self):
        assert classify(spx_close=5000, spx_sma200=4800, vix_close=15) == "bull_low_vol"

    def test_bull_high_vol(self):
        assert classify(spx_close=5000, spx_sma200=4800, vix_close=25) == "bull_high_vol"

    def test_bear_low_vol(self):
        assert classify(spx_close=4500, spx_sma200=4800, vix_close=15) == "bear_low_vol"

    def test_bear_high_vol(self):
        assert classify(spx_close=4500, spx_sma200=4800, vix_close=35) == "bear_high_vol"

    def test_vix_exactly_at_threshold_counts_as_high(self):
        """VIX >= 20 is high-vol; VIX < 20 is low-vol. Boundary is inclusive
        on the high side so the "any spike to 20" case gets the high-vol
        treatment (defensive default)."""
        assert classify(spx_close=5000, spx_sma200=4800, vix_close=VIX_THRESHOLD) == "bull_high_vol"

    def test_spx_exactly_at_sma_counts_as_bear(self):
        """SPX > SMA200 is bull; == is bear. Ties break bearish (defensive
        default: only unambiguous uptrends get the bullish flag)."""
        assert classify(spx_close=4800, spx_sma200=4800, vix_close=15) == "bear_low_vol"


@pytest.mark.parametrize("spx,sma,vix,expected", [
    (5100, 4800, 12, "bull_low_vol"),
    (5100, 4800, 22, "bull_high_vol"),
    (4200, 4800, 15, "bear_low_vol"),
    (4200, 4800, 40, "bear_high_vol"),
])
def test_classify_matrix(spx, sma, vix, expected):
    assert classify(spx, sma, vix) == expected
