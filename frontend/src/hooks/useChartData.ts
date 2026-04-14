import { useQuery } from "@tanstack/react-query";
import { marketApi } from "@/lib/api";
import type { CandlestickData, HistogramData, Time } from "lightweight-charts";

const PERIOD_INTERVAL: Record<string, string> = {
  "1W": "15m",
  "1M": "1h",
  "3M": "1d",
  "6M": "1d",
  "1Y": "1d",
  "2Y": "1wk",
  "5Y": "1wk",
};

const PERIOD_YFINANCE: Record<string, string> = {
  "1W": "5d",
  "1M": "1mo",
  "3M": "3mo",
  "6M": "6mo",
  "1Y": "1y",
  "2Y": "2y",
  "5Y": "5y",
};

function sma(data: number[], window: number): (number | null)[] {
  return data.map((_, i) => {
    if (i < window - 1) return null;
    const slice = data.slice(i - window + 1, i + 1);
    return slice.reduce((a, b) => a + b, 0) / window;
  });
}

function rsi(closes: number[], period = 14): (number | null)[] {
  const result: (number | null)[] = new Array(closes.length).fill(null);
  if (closes.length < period + 1) return result;

  let avgGain = 0;
  let avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const diff = closes[i] - closes[i - 1];
    if (diff > 0) avgGain += diff;
    else avgLoss += -diff;
  }
  avgGain /= period;
  avgLoss /= period;
  result[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);

  for (let i = period + 1; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1];
    const gain = diff > 0 ? diff : 0;
    const loss = diff < 0 ? -diff : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    result[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return result;
}

export function useChartData(ticker: string, period: string) {
  return useQuery({
    queryKey: ["chart", ticker, period],
    queryFn: async () => {
      const res = await marketApi.history(
        ticker,
        PERIOD_YFINANCE[period] ?? "3mo",
        PERIOD_INTERVAL[period] ?? "1d"
      );
      const raw = res.data.candles as {
        time: string | number;
        open: number;
        high: number;
        low: number;
        close: number;
        volume: number;
      }[];

      if (!raw?.length) return null;

      const candles: CandlestickData<Time>[] = raw.map((c) => ({
        time: c.time as Time,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }));

      const volumes: HistogramData<Time>[] = raw.map((c, i) => ({
        time: c.time as Time,
        value: c.volume,
        color:
          i === 0 || c.close >= raw[i - 1]?.close ? "#10b98133" : "#ef444433",
      }));

      const closes = raw.map((c) => c.close);
      const times = raw.map((c) => c.time as Time);

      const toSeries = (vals: (number | null)[]) =>
        vals
          .map((v, i) => (v !== null ? { time: times[i], value: v } : null))
          .filter(Boolean) as { time: Time; value: number }[];

      const sma50 = toSeries(sma(closes, 50));
      const sma200 = toSeries(sma(closes, 200));
      const rsiData = toSeries(rsi(closes, 14));

      return { candles, volumes, sma50, sma200, rsi: rsiData };
    },
    enabled: !!ticker,
    staleTime: 60_000,
  });
}
