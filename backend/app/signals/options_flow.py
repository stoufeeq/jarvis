"""
Options Flow Signal Provider.

Generates signals from the options market to identify institutional positioning.

Data source: yfinance (free, ~15-min delayed) or Unusual Whales (real-time, paid key).

Signals emitted
---------------
UNUSUAL_CALL_SWEEP  bullish  4  — OTM call with volume/OI > 3× AND significant premium
UNUSUAL_PUT_SWEEP   bearish  4  — OTM put  with volume/OI > 3× AND significant premium
BULLISH_PC_FLOW     bullish  3  — put/call volume ratio < 0.5 (heavy call buying)
BEARISH_PC_FLOW     bearish  3  — put/call volume ratio > 2.5 (heavy put buying)
BULLISH_NET_PREMIUM bullish  3  — net call premium > $500k (dollar-weighted bullish flow)
BEARISH_NET_PREMIUM bearish  3  — net put premium > $500k (dollar-weighted bearish flow)
UW_SWEEP_BULLISH    bullish  5  — Unusual Whales sweep flagged as bullish (real-time)
UW_SWEEP_BEARISH    bearish  5  — Unusual Whales sweep flagged as bearish (real-time)
"""

import logging
from datetime import UTC, datetime, timedelta

from app.models.signal import Signal, SignalDirection, SignalType
from app.services.options_data import OptionsDataService
from app.signals.base import BaseSignalProvider

log = logging.getLogger(__name__)

# Thresholds
PC_RATIO_BULLISH = 0.5    # below this → heavy call buying → bullish
PC_RATIO_BEARISH = 2.5    # above this → heavy put buying  → bearish
NET_PREMIUM_THRESHOLD = 500_000   # $500k net premium imbalance
SWEEP_PREMIUM_THRESHOLD = 25_000  # $25k minimum for a single sweep signal
EXPIRY_DAYS = 1  # options flow signals expire after 1 trading day


class OptionsFlowSignalProvider(BaseSignalProvider):
    name = "options_flow"

    async def scan(self, ticker: str) -> list[Signal]:
        try:
            svc = OptionsDataService()
            summary = await svc.get_chain_summary(ticker)
        except ValueError as exc:
            # ticker has no options (e.g. some ETFs or foreign stocks)
            log.info("Options flow skipped for %s: %s", ticker, exc)
            return []
        except Exception as exc:
            log.warning("Options flow fetch failed for %s: %s", ticker, exc)
            return []

        signals: list[Signal] = []
        expires = datetime.now(UTC) + timedelta(days=EXPIRY_DAYS)
        price = summary.get("current_price")

        def make(direction: SignalDirection, strength: int, indicators: str, rationale: str) -> Signal:
            return Signal(
                ticker=ticker.upper(),
                signal_type=SignalType.options_flow,
                direction=direction,
                strength=strength,
                entry_price=price,
                stop_loss=None,
                take_profit=None,
                indicators=indicators,
                rationale=rationale[:400],
                timeframe="1d",
                expires_at=expires,
            )

        call_vol = summary.get("call_volume", 0) or 0
        put_vol  = summary.get("put_volume",  0) or 0
        pc_ratio = summary.get("pc_ratio")
        net_call = summary.get("net_call_premium", 0) or 0
        net_put  = summary.get("net_put_premium",  0) or 0

        # ── Unusual Whales real-time sweeps (highest confidence) ─────────────
        uw_flow = summary.get("uw_flow", [])
        uw_bullish_sweeps = [f for f in uw_flow if f.get("is_sweep") and f.get("sentiment") == "bullish"]
        uw_bearish_sweeps = [f for f in uw_flow if f.get("is_sweep") and f.get("sentiment") == "bearish"]

        if uw_bullish_sweeps:
            top = max(uw_bullish_sweeps, key=lambda x: x.get("premium", 0))
            signals.append(make(
                SignalDirection.bullish, 5,
                indicators=f"UW_SWEEP_BULLISH:${top['premium']:,}:CALL:{top['strike']}:{top['expiry']}",
                rationale=(
                    f"Unusual Whales detected a real-time bullish call sweep on {ticker}: "
                    f"${top['premium']:,} premium, strike {top['strike']}, expiry {top['expiry']}. "
                    f"Total {len(uw_bullish_sweeps)} bullish sweep(s) today."
                ),
            ))

        if uw_bearish_sweeps:
            top = max(uw_bearish_sweeps, key=lambda x: x.get("premium", 0))
            signals.append(make(
                SignalDirection.bearish, 5,
                indicators=f"UW_SWEEP_BEARISH:${top['premium']:,}:PUT:{top['strike']}:{top['expiry']}",
                rationale=(
                    f"Unusual Whales detected a real-time bearish put sweep on {ticker}: "
                    f"${top['premium']:,} premium, strike {top['strike']}, expiry {top['expiry']}. "
                    f"Total {len(uw_bearish_sweeps)} bearish sweep(s) today."
                ),
            ))

        # ── yfinance-derived UNUSUAL_CALL/PUT_SWEEP — REMOVED 2026-06-03 ─────
        # June 2026 backtest sweep showed these were breakeven noise:
        # 190k outcomes at +0.09% per trade, 49.9% hit rate. Single-contract
        # bursts flagged as "unusual" by vol/OI ratio are too noisy to act on
        # — likely a mix of hedges, market-maker adjustments, and small
        # institutions. Aggregate flow (PC ratio + net premium) captures the
        # same sentiment with much better edge. The real-time Unusual Whales
        # sweeps above (strength 5) are kept since they're gated on a paid
        # feed that only enables when UNUSUAL_WHALES_API_KEY is set.

        # ── Put/Call ratio signals ────────────────────────────────────────────
        if pc_ratio is not None and call_vol + put_vol >= 500:  # need sufficient volume
            if pc_ratio <= PC_RATIO_BULLISH:
                signals.append(make(
                    SignalDirection.bullish, 3,
                    indicators=f"BULLISH_PC_FLOW:PC_RATIO={pc_ratio}:CALLS={call_vol}:PUTS={put_vol}",
                    rationale=(
                        f"Low put/call ratio of {pc_ratio} on {ticker} "
                        f"({call_vol:,} calls vs {put_vol:,} puts). "
                        f"Heavy call buying relative to puts signals near-term bullish sentiment."
                    ),
                ))
            elif pc_ratio >= PC_RATIO_BEARISH:
                signals.append(make(
                    SignalDirection.bearish, 3,
                    indicators=f"BEARISH_PC_FLOW:PC_RATIO={pc_ratio}:PUTS={put_vol}:CALLS={call_vol}",
                    rationale=(
                        f"High put/call ratio of {pc_ratio} on {ticker} "
                        f"({put_vol:,} puts vs {call_vol:,} calls). "
                        f"Heavy put buying relative to calls signals near-term bearish sentiment."
                    ),
                ))

        # ── Net premium dollar flow ────────────────────────────────────────────
        net_flow = net_call - net_put
        if abs(net_flow) >= NET_PREMIUM_THRESHOLD:
            if net_flow > 0:
                signals.append(make(
                    SignalDirection.bullish, 3,
                    indicators=f"BULLISH_NET_PREMIUM:NET_FLOW=${net_flow:,}:CALLS=${net_call:,}:PUTS=${net_put:,}",
                    rationale=(
                        f"Net call premium flow of ${net_flow:,} on {ticker} "
                        f"(${net_call:,} calls vs ${net_put:,} puts). "
                        f"Dollar-weighted options market is positioned bullish."
                    ),
                ))
            else:
                signals.append(make(
                    SignalDirection.bearish, 3,
                    indicators=f"BEARISH_NET_PREMIUM:NET_FLOW=${net_flow:,}:PUTS=${net_put:,}:CALLS=${net_call:,}",
                    rationale=(
                        f"Net put premium flow of ${abs(net_flow):,} on {ticker} "
                        f"(${net_put:,} puts vs ${net_call:,} calls). "
                        f"Dollar-weighted options market is positioned bearish."
                    ),
                ))

        return signals
