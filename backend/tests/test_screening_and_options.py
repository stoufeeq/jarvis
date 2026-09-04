"""Tests for the halal compliance screen and the options analytics helpers.

Halal screening produces a verdict the user acts on for religious reasons,
so a silently-wrong "compliant" is worse than an error. The AAOIFI ratio
boundaries (33%) and the precedence between activity screening and ratio
screening are pinned here.

Options analytics feeds IV signals. The helpers are pure numeric functions
over yfinance frames — the exact place where a NaN or an absurd IV slips
through and quietly skews a signal.
"""

import math
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.models.halal_compliance import HalalStatus
from app.services.halal_screener import (
    CASH_RATIO_MAX,
    DEBT_RATIO_MAX,
    HalalScreenerService,
    _finite,
    _normalise_label,
)
from app.services.options_analytics import (
    _closest_iv,
    _implied_move,
    _mean_finite,
)


# ── Shared helpers ────────────────────────────────────────────────────


def _info(**overrides) -> dict:
    """A minimal yfinance Ticker.info for a clean, compliant equity."""
    base = {
        "quoteType": "EQUITY",
        "sector": "Technology",
        "industry": "Software - Infrastructure",
        "marketCap": 1_000_000_000.0,
        "totalDebt": 100_000_000.0,   # 10%
        "totalCash": 100_000_000.0,   # 10%
    }
    base.update(overrides)
    return base


async def _verdict(db, ticker: str, info: dict | None) -> dict:
    """Run _compute with yfinance stubbed out."""
    svc = HalalScreenerService(db)
    svc._fetch_info = staticmethod(lambda t: info)  # type: ignore[method-assign]
    return await svc._compute(ticker)


# ── _finite ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        (1.5, 1.5),
        (0, 0.0),
        (-3, -3.0),
        ("2.5", 2.5),
        (None, None),
        ("abc", None),
        (float("nan"), None),
        (float("inf"), None),
    ],
)
def test_finite_coerces_and_rejects_non_numbers(value, expected):
    assert _finite(value) == expected


# ── Halal screen: quote types ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_etf_is_unknown_not_compliant(db):
    """Tier-1 doesn't do constituent screening, so an unlisted ETF must
    return 'unknown' rather than defaulting either way. Defaulting to
    compliant would be the dangerous failure."""
    v = await _verdict(db, "SPY", _info(quoteType="ETF"))
    assert v["status"] == HalalStatus.unknown
    assert "whitelist" in v["reason"].lower()


@pytest.mark.asyncio
async def test_mutual_fund_is_unknown(db):
    v = await _verdict(db, "VTSAX", _info(quoteType="MUTUALFUND"))
    assert v["status"] == HalalStatus.unknown


@pytest.mark.asyncio
async def test_unsupported_quote_type_is_unknown(db):
    v = await _verdict(db, "BTC-USD", _info(quoteType="CRYPTOCURRENCY"))
    assert v["status"] == HalalStatus.unknown
    assert "Unsupported quote type" in v["reason"]


@pytest.mark.asyncio
async def test_fetch_failure_is_unknown_not_a_verdict(db):
    v = await _verdict(db, "AAPL", None)
    assert v["status"] == HalalStatus.unknown


# ── Halal screen: business activity ───────────────────────────────────


@pytest.mark.asyncio
async def test_banned_industry_is_non_compliant(db):
    """Activity screening runs BEFORE ratios — a bank with clean ratios
    is still non-compliant on activity grounds."""
    v = await _verdict(db, "JPM", _info(
        industry="Banks\u2014Diversified",
        sector="Financial Services",
        totalDebt=0,     # spotless ratios
        totalCash=0,
    ))
    assert v["status"] == HalalStatus.non_compliant
    assert "Industry" in v["reason"]


@pytest.mark.parametrize(
    "industry",
    [
        "Banks\u2014Diversified",    # em-dash, as stored in the whitelist
        "Banks-Diversified",         # plain hyphen
        "Banks - Diversified",       # spaced hyphen
        "banks\u2014diversified",    # lowercase
        "  Banks\u2014Diversified ",  # padded
    ],
)
@pytest.mark.asyncio
async def test_banned_industry_matches_every_dash_spelling(db, industry):
    """yfinance is inconsistent about the separator in compound industry
    names, and the whitelist stores exactly one spelling. Before
    normalisation an exact lowercase match let the other spellings
    through, so a bank could screen COMPLIANT purely because of a dash
    character. Every variant must be rejected."""
    v = await _verdict(db, "JPM", _info(
        industry=industry,
        sector="Financial Services",
        totalDebt=0,
        totalCash=0,
    ))
    assert v["status"] == HalalStatus.non_compliant
    assert "Industry" in v["reason"]


def test_normalise_label_canonicalises_separators():
    """All dash flavours and spacings collapse to one form."""
    canonical = _normalise_label("Banks\u2014Diversified")
    for variant in (
        "Banks-Diversified",
        "Banks - Diversified",
        "banks  -  diversified",
        "BANKS\u2014DIVERSIFIED",
        "Banks\u2013Diversified",   # en-dash
    ):
        assert _normalise_label(variant) == canonical


@pytest.mark.asyncio
async def test_banned_sector_used_only_when_industry_missing(db):
    """Industry is the finer signal; sector is the fallback. A missing
    industry must still let a banned sector reject the name."""
    v = await _verdict(db, "XYZ", _info(
        industry=None, sector="Financial Services",
    ))
    assert v["status"] == HalalStatus.non_compliant
    assert "Sector" in v["reason"]


@pytest.mark.asyncio
async def test_clean_industry_proceeds_to_ratio_screen(db):
    v = await _verdict(db, "MSFT", _info())
    assert v["status"] == HalalStatus.compliant
    assert "AAOIFI" in v["reason"]


# ── Halal screen: AAOIFI ratios ───────────────────────────────────────


@pytest.mark.asyncio
async def test_debt_above_33pct_is_non_compliant(db):
    v = await _verdict(db, "XYZ", _info(
        marketCap=1_000_000_000.0,
        totalDebt=400_000_000.0,   # 40%
    ))
    assert v["status"] == HalalStatus.non_compliant
    assert "Debt" in v["reason"]
    assert v["debt_pct"] == pytest.approx(0.40)


@pytest.mark.asyncio
async def test_debt_exactly_at_threshold_is_non_compliant(db):
    """The AAOIFI rule is >= 33%, so exactly 33% fails. Getting this
    boundary backwards would wrongly pass borderline names."""
    v = await _verdict(db, "XYZ", _info(
        marketCap=1_000_000_000.0,
        totalDebt=DEBT_RATIO_MAX * 1_000_000_000.0,
    ))
    assert v["status"] == HalalStatus.non_compliant


@pytest.mark.asyncio
async def test_debt_just_below_threshold_passes(db):
    v = await _verdict(db, "XYZ", _info(
        marketCap=1_000_000_000.0,
        totalDebt=(DEBT_RATIO_MAX - 0.001) * 1_000_000_000.0,
        totalCash=0,
    ))
    assert v["status"] == HalalStatus.compliant


@pytest.mark.asyncio
async def test_cash_above_33pct_is_non_compliant(db):
    v = await _verdict(db, "XYZ", _info(
        marketCap=1_000_000_000.0,
        totalDebt=0,
        totalCash=500_000_000.0,   # 50%
    ))
    assert v["status"] == HalalStatus.non_compliant
    assert "Cash" in v["reason"]


@pytest.mark.asyncio
async def test_cash_exactly_at_threshold_is_non_compliant(db):
    v = await _verdict(db, "XYZ", _info(
        marketCap=1_000_000_000.0,
        totalDebt=0,
        totalCash=CASH_RATIO_MAX * 1_000_000_000.0,
    ))
    assert v["status"] == HalalStatus.non_compliant


@pytest.mark.asyncio
async def test_debt_checked_before_cash(db):
    """When both breach, the reported reason should be debt — stable
    messaging matters because the user reads it to understand why."""
    v = await _verdict(db, "XYZ", _info(
        marketCap=1_000_000_000.0,
        totalDebt=900_000_000.0,
        totalCash=900_000_000.0,
    ))
    assert v["status"] == HalalStatus.non_compliant
    assert "Debt" in v["reason"]


# ── Halal screen: missing data must not be guessed ────────────────────


@pytest.mark.asyncio
async def test_missing_market_cap_is_unknown(db):
    v = await _verdict(db, "XYZ", _info(marketCap=None))
    assert v["status"] == HalalStatus.unknown
    assert "market cap" in v["reason"].lower()


@pytest.mark.asyncio
async def test_zero_market_cap_is_unknown_not_divide_by_zero(db):
    v = await _verdict(db, "XYZ", _info(marketCap=0))
    assert v["status"] == HalalStatus.unknown


@pytest.mark.asyncio
async def test_missing_debt_is_unknown_not_treated_as_zero(db):
    """Treating a missing debt figure as zero would wrongly mark a
    leveraged company compliant. Must degrade to unknown."""
    v = await _verdict(db, "XYZ", _info(totalDebt=None))
    assert v["status"] == HalalStatus.unknown
    assert "Missing financials" in v["reason"]


@pytest.mark.asyncio
async def test_missing_cash_is_unknown(db):
    v = await _verdict(db, "XYZ", _info(totalCash=None))
    assert v["status"] == HalalStatus.unknown


@pytest.mark.asyncio
async def test_nan_financials_are_unknown(db):
    """yfinance returns NaN for some fields — must not propagate into
    a ratio comparison, which would silently be False for every check."""
    v = await _verdict(db, "XYZ", _info(totalDebt=float("nan")))
    assert v["status"] == HalalStatus.unknown


# ── Options: _mean_finite ─────────────────────────────────────────────


def test_mean_finite_basic_average():
    assert _mean_finite(pd.Series([0.2, 0.4, 0.6])) == pytest.approx(0.4)


def test_mean_finite_drops_nan_and_inf():
    s = pd.Series([0.2, float("nan"), 0.4, float("inf")])
    assert _mean_finite(s) == pytest.approx(0.3)


def test_mean_finite_filters_absurd_ivs():
    """IVs must sit in (0, 5). A 900% IV is a data error, not a signal —
    including it would blow out every IV-percentile comparison."""
    s = pd.Series([0.2, 0.4, 9.0, -1.0, 0.0])
    assert _mean_finite(s) == pytest.approx(0.3)


def test_mean_finite_returns_none_when_nothing_survives():
    assert _mean_finite(pd.Series([9.0, -1.0, float("nan")])) is None


def test_mean_finite_handles_empty_and_none():
    assert _mean_finite(pd.Series([], dtype=float)) is None
    assert _mean_finite(None) is None


# ── Options: _closest_iv ──────────────────────────────────────────────


def _chain(strikes, ivs) -> pd.DataFrame:
    return pd.DataFrame({"strike": strikes, "impliedVolatility": ivs})


def test_closest_iv_picks_nearest_strike():
    df = _chain([90, 100, 110], [0.30, 0.25, 0.35])
    assert _closest_iv(df, 101) == pytest.approx(0.25)
    assert _closest_iv(df, 109) == pytest.approx(0.35)


def test_closest_iv_rejects_out_of_range_iv():
    """Nearest strike wins, but if its IV is absurd we return None rather
    than handing a 700% IV to the signal engine."""
    df = _chain([100], [7.0])
    assert _closest_iv(df, 100) is None


def test_closest_iv_rejects_zero_and_negative():
    assert _closest_iv(_chain([100], [0.0]), 100) is None
    assert _closest_iv(_chain([100], [-0.2]), 100) is None


def test_closest_iv_empty_chain():
    assert _closest_iv(_chain([], []), 100) is None


def test_closest_iv_handles_non_numeric():
    assert _closest_iv(_chain([100], ["n/a"]), 100) is None


# ── Options: _implied_move ────────────────────────────────────────────


def _quotes_df(strike, bid, ask, last=0.0) -> pd.DataFrame:
    return pd.DataFrame([{"strike": strike, "bid": bid, "ask": ask, "lastPrice": last}])


def test_implied_move_from_atm_straddle():
    """(call_mid + put_mid) / spot, as a percentage. Call mid 5, put mid 5,
    spot 100 → 10%."""
    calls = _quotes_df(100, bid=4.0, ask=6.0)
    puts = _quotes_df(100, bid=4.0, ask=6.0)
    assert _implied_move(calls, puts, spot=100.0) == pytest.approx(10.0)


def test_implied_move_falls_back_to_last_price_when_no_bid_ask():
    """Illiquid contracts often have 0/0 bid-ask; lastPrice is the fallback."""
    calls = _quotes_df(100, bid=0, ask=0, last=5.0)
    puts = _quotes_df(100, bid=0, ask=0, last=5.0)
    assert _implied_move(calls, puts, spot=100.0) == pytest.approx(10.0)


def test_implied_move_none_when_a_side_has_no_price():
    calls = _quotes_df(100, bid=0, ask=0, last=0)
    puts = _quotes_df(100, bid=4.0, ask=6.0)
    assert _implied_move(calls, puts, spot=100.0) is None


def test_implied_move_none_on_empty_chains():
    empty = pd.DataFrame(columns=["strike", "bid", "ask", "lastPrice"])
    assert _implied_move(empty, _quotes_df(100, 4, 6), spot=100.0) is None
    assert _implied_move(_quotes_df(100, 4, 6), empty, spot=100.0) is None


def test_implied_move_none_on_invalid_spot():
    calls = _quotes_df(100, bid=4.0, ask=6.0)
    puts = _quotes_df(100, bid=4.0, ask=6.0)
    assert _implied_move(calls, puts, spot=0.0) is None
    assert _implied_move(calls, puts, spot=-10.0) is None
