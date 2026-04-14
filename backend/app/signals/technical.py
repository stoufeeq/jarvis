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

import pandas as pd
import ta
import ta.momentum
import ta.trend
import ta.volatility
import ta.volume

from app.models.signal import Signal, SignalDirection, SignalType
from app.services.market_data import MarketDataService
from app.signals.base import BaseSignalProvider


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

        return signals
