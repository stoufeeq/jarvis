"""
Fundamental Signal Provider.

Uses yfinance Ticker.info to fetch valuation, growth, and quality metrics.
No paid API required — all data is sourced from Yahoo Finance (15-min delayed).

Signals fired:
  Valuation
  - PE_CHEAP        P/E < 15 and positive earnings          → bullish 3
  - PE_EXPENSIVE    P/E > 40                                → bearish 3
  - PB_CHEAP        P/B < 1.0 (trading below book value)   → bullish 3
  - PB_EXPENSIVE    P/B > 10                                → bearish 2
  - PEG_CHEAP       PEG < 1.0 (growth at reasonable price) → bullish 3
  - PEG_EXPENSIVE   PEG > 3.0 (paying too much for growth) → bearish 2

  Growth
  - EARNINGS_GROWTH_STRONG   YoY EPS growth > 25 %          → bullish 4
  - EARNINGS_GROWTH_DECLINE  YoY EPS growth < -15 %         → bearish 4
  - REVENUE_GROWTH_STRONG    YoY revenue growth > 20 %      → bullish 3
  - REVENUE_GROWTH_DECLINE   YoY revenue growth < -10 %     → bearish 3

  Quality / Health
  - HIGH_DEBT        Debt/Equity > 200 %                    → bearish 3
  - LOW_DEBT         Debt/Equity < 30 % with positive FCF   → bullish 2
  - STRONG_MARGINS   Net margin > 20 %                      → bullish 2
  - WEAK_MARGINS     Net margin < 0 % (loss-making)         → bearish 3
  - HIGH_ROE         ROE > 20 %                             → bullish 2
  - FREE_CASH_FLOW_POSITIVE  FCF yield > 4 %               → bullish 3
  - FREE_CASH_FLOW_NEGATIVE  Negative FCF                   → bearish 2

Signals expire after 30 days (fundamentals change slowly).
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from functools import partial

import yfinance as yf

from app.models.signal import Signal, SignalDirection, SignalType
from app.signals.base import BaseSignalProvider

log = logging.getLogger(__name__)

EXPIRES_DAYS = 30


def _safe(info: dict, key: str) -> float | None:
    v = info.get(key)
    if v is None:
        return None
    try:
        f = float(v)
        return None if (f != f) else f   # filter NaN
    except (TypeError, ValueError):
        return None


class FundamentalSignalProvider(BaseSignalProvider):
    async def scan(self, ticker: str) -> list[Signal]:
        try:
            loop = asyncio.get_event_loop()
            info: dict = await loop.run_in_executor(
                None, partial(self._fetch_info, ticker)
            )
        except Exception as exc:
            log.warning("FundamentalSignalProvider: failed to fetch info for %s: %s", ticker, exc)
            return []

        if not info:
            return []

        signals: list[Signal] = []
        expires = datetime.now(UTC) + timedelta(days=EXPIRES_DAYS)
        current_price = _safe(info, "currentPrice") or _safe(info, "regularMarketPrice")

        def make(
            direction: SignalDirection,
            strength: int,
            indicators: str,
            rationale: str,
        ) -> Signal:
            return Signal(
                ticker=ticker,
                signal_type=SignalType.fundamental,
                direction=direction,
                strength=strength,
                entry_price=current_price,
                indicators=indicators,
                rationale=rationale,
                timeframe="swing",
                expires_at=expires,
            )

        # ── Valuation ─────────────────────────────────────────────────────────

        pe = _safe(info, "trailingPE")
        if pe is not None and pe > 0:
            if pe < 15:
                signals.append(make(
                    SignalDirection.bullish, 3,
                    f"PE_CHEAP:{pe:.1f}",
                    f"Trailing P/E of {pe:.1f}x is below 15 — stock appears undervalued on earnings.",
                ))
            elif pe > 40:
                signals.append(make(
                    SignalDirection.bearish, 3,
                    f"PE_EXPENSIVE:{pe:.1f}",
                    f"Trailing P/E of {pe:.1f}x exceeds 40 — rich valuation leaves little margin of safety.",
                ))

        pb = _safe(info, "priceToBook")
        if pb is not None and pb > 0:
            if pb < 1.0:
                signals.append(make(
                    SignalDirection.bullish, 3,
                    f"PB_CHEAP:{pb:.2f}",
                    f"P/B of {pb:.2f}x — trading below book value. Asset-backed downside protection.",
                ))
            elif pb > 10:
                signals.append(make(
                    SignalDirection.bearish, 2,
                    f"PB_EXPENSIVE:{pb:.1f}",
                    f"P/B of {pb:.1f}x — priced well above book value; requires strong growth to justify.",
                ))

        peg = _safe(info, "trailingPegRatio")
        if peg is not None and peg > 0:
            if peg < 1.0:
                signals.append(make(
                    SignalDirection.bullish, 3,
                    f"PEG_CHEAP:{peg:.2f}",
                    f"PEG ratio of {peg:.2f} — growth available at a reasonable price (< 1.0).",
                ))
            elif peg > 3.0:
                signals.append(make(
                    SignalDirection.bearish, 2,
                    f"PEG_EXPENSIVE:{peg:.2f}",
                    f"PEG ratio of {peg:.2f} — paying a high premium relative to expected earnings growth.",
                ))

        # ── Growth ────────────────────────────────────────────────────────────

        eps_growth = _safe(info, "earningsGrowth")  # YoY, as a decimal (0.25 = 25%)
        if eps_growth is not None:
            pct = eps_growth * 100
            if pct > 25:
                signals.append(make(
                    SignalDirection.bullish, 4,
                    f"EARNINGS_GROWTH_STRONG:{pct:.1f}%",
                    f"YoY earnings growth of {pct:.1f}% — strong acceleration in profitability.",
                ))
            elif pct < -15:
                signals.append(make(
                    SignalDirection.bearish, 4,
                    f"EARNINGS_GROWTH_DECLINE:{pct:.1f}%",
                    f"YoY earnings declined {abs(pct):.1f}% — deteriorating profitability.",
                ))

        rev_growth = _safe(info, "revenueGrowth")  # YoY, as a decimal
        if rev_growth is not None:
            pct = rev_growth * 100
            if pct > 20:
                signals.append(make(
                    SignalDirection.bullish, 3,
                    f"REVENUE_GROWTH_STRONG:{pct:.1f}%",
                    f"Revenue growing {pct:.1f}% YoY — strong top-line momentum.",
                ))
            elif pct < -10:
                signals.append(make(
                    SignalDirection.bearish, 3,
                    f"REVENUE_GROWTH_DECLINE:{pct:.1f}%",
                    f"Revenue shrank {abs(pct):.1f}% YoY — top-line deterioration.",
                ))

        # ── Quality / Balance Sheet ───────────────────────────────────────────

        de = _safe(info, "debtToEquity")  # expressed as percentage (200 = 200%)
        if de is not None:
            if de > 200:
                signals.append(make(
                    SignalDirection.bearish, 3,
                    f"HIGH_DEBT:{de:.0f}%",
                    f"Debt/Equity of {de:.0f}% — heavy leverage increases financial risk.",
                ))
            elif de < 30:
                fcf = _safe(info, "freeCashflow")
                if fcf is not None and fcf > 0:
                    signals.append(make(
                        SignalDirection.bullish, 2,
                        f"LOW_DEBT:{de:.0f}%",
                        f"Debt/Equity of only {de:.0f}% with positive free cash flow — clean balance sheet.",
                    ))

        net_margin = _safe(info, "profitMargins")
        if net_margin is not None:
            pct = net_margin * 100
            if pct > 20:
                signals.append(make(
                    SignalDirection.bullish, 2,
                    f"STRONG_MARGINS:{pct:.1f}%",
                    f"Net profit margin of {pct:.1f}% — high-quality, capital-efficient business.",
                ))
            elif pct < 0:
                signals.append(make(
                    SignalDirection.bearish, 3,
                    f"WEAK_MARGINS:{pct:.1f}%",
                    f"Net margin is negative ({pct:.1f}%) — company is currently loss-making.",
                ))

        roe = _safe(info, "returnOnEquity")
        if roe is not None:
            pct = roe * 100
            if pct > 20:
                signals.append(make(
                    SignalDirection.bullish, 2,
                    f"HIGH_ROE:{pct:.1f}%",
                    f"Return on equity of {pct:.1f}% — management is efficiently deploying capital.",
                ))

        fcf = _safe(info, "freeCashflow")
        market_cap = _safe(info, "marketCap")
        if fcf is not None and market_cap and market_cap > 0:
            fcf_yield = (fcf / market_cap) * 100
            if fcf_yield > 4:
                signals.append(make(
                    SignalDirection.bullish, 3,
                    f"FCF_YIELD:{fcf_yield:.1f}%",
                    f"Free cash flow yield of {fcf_yield:.1f}% — strong cash generation relative to market cap.",
                ))
            elif fcf < 0:
                signals.append(make(
                    SignalDirection.bearish, 2,
                    f"FCF_NEGATIVE",
                    f"Negative free cash flow — company is burning cash; watch for dilution or debt increase.",
                ))

        return signals

    @staticmethod
    def _fetch_info(ticker: str) -> dict:
        return yf.Ticker(ticker).info or {}
