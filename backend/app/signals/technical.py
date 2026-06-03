"""
Technical Analysis Signal Provider.

Signals fired:
  - RSI oversold / overbought  (< 30 / > 70)
  - RSI momentum cross 50      (cross above / below 50-line)
  - MACD crossover             (within last 5 bars)
  - Golden / Death cross       (50/200 SMA, within last 5 bars)
  - Price vs 50-day SMA        (recent cross above/below)
  - Bollinger Band bounce / rejection
  - Volume spike               (> 2× 20-day average)

Stop-loss: 1.5× ATR from entry.
Take-profit: 2× the risk (1:2 R:R).
"""

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import ta
import ta.momentum
import ta.trend
import ta.volatility
import ta.volume

from app.models.signal import Signal, SignalDirection, SignalType
from app.services.market_data import MarketDataService
from app.signals.base import BaseSignalProvider

# ── Flag pattern detection ────────────────────────────────────────────────
# Bull/bear flag = strong directional move ("pole") followed by tight
# consolidation against that move ("flag"), broken by a continuation move
# in the original direction. Conservative thresholds — these fire rarely
# but with high conviction.

FLAG_POLE_MIN_PCT = 0.10        # at least 10% pole (over ~10 bars)
FLAG_RANGE_MAX_PCT = 0.05       # consolidation range < 5% of price
FLAG_VOL_RATIO_MIN = 1.2        # breakout day volume > 1.2× 20-day avg
FLAG_CONSOLIDATION_BARS = 4     # tight range over the last 4 bars before today


def _detect_bull_flag(df: pd.DataFrame) -> dict | None:
    """Bull flag breakout on the latest bar — pole up, tight flat/down
    flag, today's close breaks above the flag's highs on rising volume.

    Returns descriptor dict if pattern fires; None otherwise.
    """
    if len(df) < 15:
        return None

    close = df["Close"].to_numpy()
    high = df["High"].to_numpy()
    low = df["Low"].to_numpy()
    vol = df["Volume"].to_numpy()
    n = FLAG_CONSOLIDATION_BARS

    # Pole: rise from the low of bars 10..n+1 ago into the high of bars 5..n+1 ago
    pole_low = float(np.min(close[-(10 + n + 1) : -(n + 1)]))
    pole_high = float(np.max(close[-(5 + n + 1) : -(n + 1)]))
    if pole_low <= 0:
        return None
    pole_pct = (pole_high - pole_low) / pole_low
    if pole_pct < FLAG_POLE_MIN_PCT:
        return None

    # Flag: the n bars immediately before today. Tight range + flat/down slope.
    flag_close = close[-(n + 1) : -1]
    flag_high_max = float(np.max(high[-(n + 1) : -1]))
    flag_low_min = float(np.min(low[-(n + 1) : -1]))
    flag_mean = float(np.mean(flag_close))
    if flag_mean <= 0:
        return None
    flag_range_pct = (flag_high_max - flag_low_min) / flag_mean
    if flag_range_pct > FLAG_RANGE_MAX_PCT:
        return None
    # Slope: linear fit over the flag bars; should be <= 0 (flag pulls back
    # against the pole, or sits flat). Allow small positive noise.
    slope = float(np.polyfit(np.arange(n), flag_close, 1)[0])
    if slope > flag_mean * 0.005:  # > 0.5% per bar uptrend disqualifies
        return None

    # Breakout: today's close above the flag's highest close, with volume.
    today_close = float(close[-1])
    breakout_level = float(np.max(flag_close))
    if today_close <= breakout_level:
        return None

    avg_vol = float(np.mean(vol[-21:-1])) if len(vol) >= 21 else float(np.mean(vol[:-1]))
    if avg_vol <= 0 or vol[-1] < avg_vol * FLAG_VOL_RATIO_MIN:
        return None

    return {
        "pole_pct": pole_pct,
        "flag_range_pct": flag_range_pct,
        "breakout_level": breakout_level,
        "today_close": today_close,
        "vol_ratio": vol[-1] / avg_vol,
    }


def _detect_bear_flag(df: pd.DataFrame) -> dict | None:
    """Bear flag breakdown on the latest bar — pole down, tight flat/up
    flag, today's close breaks below the flag's lows on rising volume.
    Mirror of the bull flag.
    """
    if len(df) < 15:
        return None

    close = df["Close"].to_numpy()
    high = df["High"].to_numpy()
    low = df["Low"].to_numpy()
    vol = df["Volume"].to_numpy()
    n = FLAG_CONSOLIDATION_BARS

    # Pole: drop from the high of bars 10..n+1 ago into the low of bars 5..n+1 ago
    pole_high = float(np.max(close[-(10 + n + 1) : -(n + 1)]))
    pole_low = float(np.min(close[-(5 + n + 1) : -(n + 1)]))
    if pole_high <= 0:
        return None
    pole_pct = (pole_high - pole_low) / pole_high
    if pole_pct < FLAG_POLE_MIN_PCT:
        return None

    # Flag: tight range with flat/up slope (against the down pole).
    flag_close = close[-(n + 1) : -1]
    flag_high_max = float(np.max(high[-(n + 1) : -1]))
    flag_low_min = float(np.min(low[-(n + 1) : -1]))
    flag_mean = float(np.mean(flag_close))
    if flag_mean <= 0:
        return None
    flag_range_pct = (flag_high_max - flag_low_min) / flag_mean
    if flag_range_pct > FLAG_RANGE_MAX_PCT:
        return None
    slope = float(np.polyfit(np.arange(n), flag_close, 1)[0])
    if slope < -flag_mean * 0.005:  # >0.5% per bar drop disqualifies
        return None

    # Breakdown: today's close below the flag's lowest close, with volume.
    today_close = float(close[-1])
    breakdown_level = float(np.min(flag_close))
    if today_close >= breakdown_level:
        return None

    avg_vol = float(np.mean(vol[-21:-1])) if len(vol) >= 21 else float(np.mean(vol[:-1]))
    if avg_vol <= 0 or vol[-1] < avg_vol * FLAG_VOL_RATIO_MIN:
        return None

    return {
        "pole_pct": pole_pct,
        "flag_range_pct": flag_range_pct,
        "breakdown_level": breakdown_level,
        "today_close": today_close,
        "vol_ratio": vol[-1] / avg_vol,
    }


class TechnicalSignalProvider(BaseSignalProvider):
    ATR_MULTIPLIER_SL = 1.5
    RR_RATIO = 2.0
    # How many recent bars to look back for a crossover event
    CROSSOVER_LOOKBACK = 5

    async def scan(self, ticker: str) -> list[Signal]:
        mds = MarketDataService()
        # Use 2y so SMA200 has enough history
        df = await mds.get_ohlcv_dataframe(ticker, period="2y", interval="1d")

        if df is None or len(df) < 50:
            return []

        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]

        # ── Indicators ────────────────────────────────────────────────────────
        rsi = ta.momentum.RSIIndicator(close=close, window=14).rsi()
        macd_obj = ta.trend.MACD(close=close, window_fast=12, window_slow=26, window_sign=9)
        macd_line = macd_obj.macd()
        macd_signal_line = macd_obj.macd_signal()
        sma50 = ta.trend.SMAIndicator(close=close, window=50).sma_indicator()
        sma200 = ta.trend.SMAIndicator(close=close, window=200).sma_indicator()
        bb = ta.volatility.BollingerBands(close=close, window=20)
        bb_upper = bb.bollinger_hband()
        bb_lower = bb.bollinger_lband()
        atr = ta.volatility.AverageTrueRange(
            high=high, low=low, close=close, window=14
        ).average_true_range()
        vol_ma20 = volume.rolling(20).mean()

        current_price = float(close.iloc[-1])
        atr_val = float(atr.iloc[-1]) if not atr.empty else 0.0

        signals: list[Signal] = []

        def make_signal(
            direction: SignalDirection,
            strength: int,
            indicators: str,
            rationale: str,
        ) -> Signal:
            sl_offset = atr_val * self.ATR_MULTIPLIER_SL
            if direction == SignalDirection.bullish:
                stop_loss = current_price - sl_offset
                take_profit = current_price + sl_offset * self.RR_RATIO
            else:
                stop_loss = current_price + sl_offset
                take_profit = current_price - sl_offset * self.RR_RATIO

            return Signal(
                ticker=ticker,
                signal_type=SignalType.technical,
                direction=direction,
                strength=strength,
                entry_price=current_price,
                stop_loss=round(stop_loss, 4),
                take_profit=round(take_profit, 4),
                indicators=indicators,
                rationale=rationale,
                timeframe="1d",
                expires_at=datetime.now(UTC) + timedelta(days=5),
            )

        def crossed_above(series_a: pd.Series, series_b: pd.Series, lookback: int) -> bool:
            """True if series_a crossed above series_b within the last `lookback` bars."""
            window = lookback + 1
            a = series_a.dropna().iloc[-window:]
            b = series_b.dropna().iloc[-window:]
            if len(a) < 2 or len(b) < 2:
                return False
            # Align by position (both should be same length after dropna on same index)
            aligned = pd.DataFrame({"a": a, "b": b}).dropna()
            if len(aligned) < 2:
                return False
            for i in range(1, len(aligned)):
                if aligned["a"].iloc[i - 1] < aligned["b"].iloc[i - 1] and \
                   aligned["a"].iloc[i] >= aligned["b"].iloc[i]:
                    return True
            return False

        def crossed_below(series_a: pd.Series, series_b: pd.Series, lookback: int) -> bool:
            """True if series_a crossed below series_b within the last `lookback` bars."""
            window = lookback + 1
            a = series_a.dropna().iloc[-window:]
            b = series_b.dropna().iloc[-window:]
            if len(a) < 2 or len(b) < 2:
                return False
            aligned = pd.DataFrame({"a": a, "b": b}).dropna()
            if len(aligned) < 2:
                return False
            for i in range(1, len(aligned)):
                if aligned["a"].iloc[i - 1] > aligned["b"].iloc[i - 1] and \
                   aligned["a"].iloc[i] <= aligned["b"].iloc[i]:
                    return True
            return False

        # ── RSI extreme levels ────────────────────────────────────────────────
        if not rsi.empty:
            rsi_val = float(rsi.iloc[-1])
            if rsi_val < 30:
                signals.append(make_signal(
                    SignalDirection.bullish, 4,
                    f"RSI={rsi_val:.1f}",
                    f"RSI oversold at {rsi_val:.1f} (< 30). Mean-reversion long opportunity.",
                ))
            elif rsi_val > 70:
                signals.append(make_signal(
                    SignalDirection.bearish, 4,
                    f"RSI={rsi_val:.1f}",
                    f"RSI overbought at {rsi_val:.1f} (> 70). Potential short or exit.",
                ))

        # ── RSI cross 50-line (momentum shift) ────────────────────────────────
        if len(rsi.dropna()) >= self.CROSSOVER_LOOKBACK + 1:
            fifty = pd.Series([50.0] * len(rsi), index=rsi.index)
            if crossed_above(rsi, fifty, self.CROSSOVER_LOOKBACK):
                signals.append(make_signal(
                    SignalDirection.bullish, 2,
                    "RSI_CROSS_50_UP",
                    f"RSI crossed above 50 (now {float(rsi.iloc[-1]):.1f}) — momentum turning bullish.",
                ))
            elif crossed_below(rsi, fifty, self.CROSSOVER_LOOKBACK):
                signals.append(make_signal(
                    SignalDirection.bearish, 2,
                    "RSI_CROSS_50_DOWN",
                    f"RSI crossed below 50 (now {float(rsi.iloc[-1]):.1f}) — momentum turning bearish.",
                ))

        # ── MACD crossover (within lookback window) ───────────────────────────
        if crossed_above(macd_line, macd_signal_line, self.CROSSOVER_LOOKBACK):
            signals.append(make_signal(
                SignalDirection.bullish, 4,
                "MACD_CROSS_UP",
                "MACD line crossed above signal line — bullish momentum shift.",
            ))
        elif crossed_below(macd_line, macd_signal_line, self.CROSSOVER_LOOKBACK):
            signals.append(make_signal(
                SignalDirection.bearish, 4,
                "MACD_CROSS_DOWN",
                "MACD line crossed below signal line — bearish momentum shift.",
            ))

        # ── Price vs 50-day SMA cross ─────────────────────────────────────────
        if len(sma50.dropna()) >= self.CROSSOVER_LOOKBACK + 1:
            if crossed_above(close, sma50, self.CROSSOVER_LOOKBACK):
                signals.append(make_signal(
                    SignalDirection.bullish, 3,
                    "PRICE_CROSS_SMA50_UP",
                    f"Price crossed above 50-day SMA (${float(sma50.iloc[-1]):.2f}) — short-term trend turning bullish.",
                ))
            elif crossed_below(close, sma50, self.CROSSOVER_LOOKBACK):
                signals.append(make_signal(
                    SignalDirection.bearish, 3,
                    "PRICE_CROSS_SMA50_DOWN",
                    f"Price crossed below 50-day SMA (${float(sma50.iloc[-1]):.2f}) — short-term trend turning bearish.",
                ))

        # ── Golden / Death cross (50/200 SMA) ────────────────────────────────
        sma200_valid = sma200.dropna()
        if len(sma200_valid) >= self.CROSSOVER_LOOKBACK + 1:
            if crossed_above(sma50, sma200, self.CROSSOVER_LOOKBACK):
                signals.append(make_signal(
                    SignalDirection.bullish, 5,
                    "GOLDEN_CROSS",
                    "50-day SMA crossed above 200-day SMA — golden cross, strong long-term bullish signal.",
                ))
            elif crossed_below(sma50, sma200, self.CROSSOVER_LOOKBACK):
                signals.append(make_signal(
                    SignalDirection.bearish, 5,
                    "DEATH_CROSS",
                    "50-day SMA crossed below 200-day SMA — death cross, strong long-term bearish signal.",
                ))

        # ── Bollinger Band bounce / rejection ─────────────────────────────────
        if len(bb_lower.dropna()) >= 2:
            prev_close = float(close.iloc[-2])
            prev_lower = float(bb_lower.iloc[-2])
            prev_upper = float(bb_upper.iloc[-2])
            curr_lower = float(bb_lower.iloc[-1])
            curr_upper = float(bb_upper.iloc[-1])

            if prev_close <= prev_lower and current_price > curr_lower:
                signals.append(make_signal(
                    SignalDirection.bullish, 3,
                    "BB_LOWER_BOUNCE",
                    f"Price bounced off lower Bollinger Band (${curr_lower:.2f}). Potential mean-reversion long.",
                ))
            elif prev_close >= prev_upper and current_price < curr_upper:
                signals.append(make_signal(
                    SignalDirection.bearish, 3,
                    "BB_UPPER_REJECT",
                    f"Price rejected from upper Bollinger Band (${curr_upper:.2f}). Potential reversal short.",
                ))

        # ── Volume spike ──────────────────────────────────────────────────────
        if len(vol_ma20.dropna()) >= 1:
            avg_vol = float(vol_ma20.iloc[-1])
            curr_vol = float(volume.iloc[-1])
            if avg_vol > 0 and curr_vol >= avg_vol * 2.0:
                vol_ratio = curr_vol / avg_vol
                # Direction: bullish if price closed up, bearish if closed down
                prev_close_val = float(close.iloc[-2])
                direction = SignalDirection.bullish if current_price >= prev_close_val else SignalDirection.bearish
                signals.append(make_signal(
                    direction, 3,
                    f"VOL_SPIKE_{vol_ratio:.1f}x",
                    f"Volume spike: {vol_ratio:.1f}× the 20-day average. "
                    f"Unusual interest — {'buying' if direction == SignalDirection.bullish else 'selling'} pressure.",
                ))

        # ── Bull / Bear flag breakout ─────────────────────────────────────────
        # Strong directional move + tight consolidation + breakout on volume.
        # Rare-fire, high-conviction pattern — strength 4.
        bull_flag = _detect_bull_flag(df)
        if bull_flag is not None:
            signals.append(make_signal(
                SignalDirection.bullish, 4,
                "BULL_FLAG_BREAKOUT",
                f"Bull flag breakout: {bull_flag['pole_pct'] * 100:.1f}% pole, "
                f"{bull_flag['flag_range_pct'] * 100:.1f}% flag range, "
                f"closed above ${bull_flag['breakout_level']:.2f} on "
                f"{bull_flag['vol_ratio']:.1f}× avg volume. Continuation long setup.",
            ))

        bear_flag = _detect_bear_flag(df)
        if bear_flag is not None:
            signals.append(make_signal(
                SignalDirection.bearish, 4,
                "BEAR_FLAG_BREAKDOWN",
                f"Bear flag breakdown: {bear_flag['pole_pct'] * 100:.1f}% pole, "
                f"{bear_flag['flag_range_pct'] * 100:.1f}% flag range, "
                f"closed below ${bear_flag['breakdown_level']:.2f} on "
                f"{bear_flag['vol_ratio']:.1f}× avg volume. Continuation short setup.",
            ))

        return signals
