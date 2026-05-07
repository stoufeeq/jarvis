"""
IV (Implied Volatility) Analytics Signal Provider.

Generates signals from options pricing math (Black-Scholes-derived metrics)
to surface what the options market is expecting. Educational and useful
for cross-confirming directional signals from other providers.

Signals emitted (all use signal_type = "options_flow")
------------------------------------------------------
IV_CRUSH_WARNING        bearish  3  — Earnings ≤7d + IV/HV > 1.5 → premium will collapse post-earnings
IV_CHEAP_VOL            bullish  3  — IV/HV < 0.8 → options are cheap; good time to buy directional premium
IV_EXPENSIVE_VOL        bearish  3  — IV/HV > 1.6 → options are rich; premium-selling edge
PUT_CALL_SKEW_HIGH      bearish  4  — Put IV ≥ Call IV + 8% (vol skew) → fear premium for downside protection
IMPLIED_MOVE_LARGE      neutral  2  — Implied move > 6% by next monthly expiry → big event priced in
"""

import logging
from datetime import UTC, datetime, timedelta

from app.models.signal import Signal, SignalDirection, SignalType
from app.services.options_analytics import OptionsAnalyticsService
from app.signals.base import BaseSignalProvider

log = logging.getLogger(__name__)

# Thresholds
EARNINGS_WINDOW_DAYS = 7      # IV crush only if earnings within this many days
IV_HV_CRUSH_THRESHOLD = 1.5   # IV ≥ 1.5× HV with earnings imminent
IV_HV_CHEAP_THRESHOLD = 0.8   # IV ≤ 0.8× HV → vol is bargain
IV_HV_EXPENSIVE_THRESHOLD = 1.6  # IV ≥ 1.6× HV → vol is rich
SKEW_THRESHOLD = 0.08         # Put IV - Call IV ≥ 8 vol points → significant fear premium
IMPLIED_MOVE_LARGE_THRESHOLD = 6.0  # > 6% expected move by next monthly expiry

# Signals expire one trading day after generation (intraday context only)
EXPIRY_DAYS = 1


class IVAnalyticsSignalProvider(BaseSignalProvider):
    name = "iv_analytics"

    async def scan(self, ticker: str) -> list[Signal]:
        try:
            iv = await OptionsAnalyticsService.get_iv_summary(ticker)
        except Exception as exc:
            log.warning("IV analytics fetch failed for %s: %s", ticker, exc)
            return []

        if not iv or iv.get("atm_iv") is None:
            # No options listed for this ticker, or insufficient data
            return []

        signals: list[Signal] = []
        expires_at = datetime.now(UTC) + timedelta(days=EXPIRY_DAYS)

        atm_iv = iv["atm_iv"]
        hv_20 = iv.get("hv_20")
        iv_hv = iv.get("iv_hv_ratio")
        skew = iv.get("skew")
        implied_move = iv.get("implied_move_pct")
        days_to_earnings = iv.get("days_to_earnings")

        common_indicators = (
            f"atm_iv={atm_iv:.2%}"
            + (f",hv20={hv_20:.2%}" if hv_20 else "")
            + (f",iv_hv={iv_hv:.2f}" if iv_hv else "")
            + (f",implied_move={implied_move:.2f}%" if implied_move else "")
            + (f",skew={skew:.2%}" if skew else "")
        )

        # 1. IV_CRUSH_WARNING — pre-earnings vol risk
        if (
            days_to_earnings is not None
            and days_to_earnings <= EARNINGS_WINDOW_DAYS
            and iv_hv is not None
            and iv_hv >= IV_HV_CRUSH_THRESHOLD
        ):
            signals.append(Signal(
                ticker=ticker,
                signal_type=SignalType.options_flow,
                direction=SignalDirection.bearish,  # bearish for option BUYERS specifically
                strength=3,
                rationale=(
                    f"IV crush risk: earnings in {days_to_earnings}d, "
                    f"IV is {iv_hv:.1f}× historical vol. "
                    f"Buying long premium here often loses despite correct direction "
                    f"because IV will collapse post-earnings."
                ),
                indicators=f"IV_CRUSH_WARNING,{common_indicators},days_to_earnings={days_to_earnings}",
                timeframe="1d",
                expires_at=expires_at,
            ))

        # 2. IV_CHEAP_VOL — options are a bargain
        if iv_hv is not None and iv_hv <= IV_HV_CHEAP_THRESHOLD:
            signals.append(Signal(
                ticker=ticker,
                signal_type=SignalType.options_flow,
                direction=SignalDirection.bullish,
                strength=3,
                rationale=(
                    f"Options are unusually cheap: IV is {iv_hv:.2f}× historical vol. "
                    f"If you have a directional thesis, premium pricing favours buyers right now."
                ),
                indicators=f"IV_CHEAP_VOL,{common_indicators}",
                timeframe="1d",
                expires_at=expires_at,
            ))

        # 3. IV_EXPENSIVE_VOL — options are rich
        if iv_hv is not None and iv_hv >= IV_HV_EXPENSIVE_THRESHOLD:
            signals.append(Signal(
                ticker=ticker,
                signal_type=SignalType.options_flow,
                direction=SignalDirection.bearish,
                strength=3,
                rationale=(
                    f"Options are unusually expensive: IV is {iv_hv:.2f}× historical vol. "
                    f"Premium sellers (cash-secured puts, covered calls) have an edge here."
                ),
                indicators=f"IV_EXPENSIVE_VOL,{common_indicators}",
                timeframe="1d",
                expires_at=expires_at,
            ))

        # 4. PUT_CALL_SKEW_HIGH — fear premium for downside
        if skew is not None and skew >= SKEW_THRESHOLD:
            signals.append(Signal(
                ticker=ticker,
                signal_type=SignalType.options_flow,
                direction=SignalDirection.bearish,
                strength=4,
                rationale=(
                    f"Significant put-call skew: puts trading at {skew:.1%} higher IV than equivalent calls. "
                    f"The options market is paying a fear premium for downside protection — "
                    f"often precedes drops or marks panic-bottom sentiment."
                ),
                indicators=f"PUT_CALL_SKEW_HIGH,{common_indicators}",
                timeframe="1d",
                expires_at=expires_at,
            ))

        # 5. IMPLIED_MOVE_LARGE — big move priced in (no direction)
        if implied_move is not None and implied_move >= IMPLIED_MOVE_LARGE_THRESHOLD:
            signals.append(Signal(
                ticker=ticker,
                signal_type=SignalType.options_flow,
                direction=SignalDirection.neutral,
                strength=2,
                rationale=(
                    f"Options market is pricing in a {implied_move:.1f}% move by "
                    f"{iv['expiry_used']}. Significant event or news flow expected — "
                    f"size positions accordingly."
                ),
                indicators=f"IMPLIED_MOVE_LARGE,{common_indicators}",
                timeframe="1d",
                expires_at=expires_at,
            ))

        return signals
