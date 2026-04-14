"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { signalsApi } from "@/lib/api";
import { CandlestickChart } from "@/components/charts/CandlestickChart";
import { useChartData } from "@/hooks/useChartData";
import { formatCurrency, formatPct, pnlColor } from "@/lib/utils";
import type { Quote, Signal } from "@/types";

const PERIODS = ["1W", "1M", "3M", "6M", "1Y", "2Y", "5Y"];

interface Props {
  ticker: string;
  quote?: Quote;
}

export function InlineChart({ ticker, quote }: Props) {
  const [period, setPeriod] = useState("3M");
  const { data: chartData, isLoading: chartLoading } = useChartData(ticker, period);

  const { data: signals = [] } = useQuery<Signal[]>({
    queryKey: ["signals", ticker],
    queryFn: () => signalsApi.list({ ticker, limit: 10 }).then((r) => r.data),
  });

  return (
    <div className="bg-card/50 p-4 space-y-4">
      {/* Mini header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-baseline gap-3">
          <span className="text-lg font-bold">{ticker}</span>
          {quote && (
            <>
              <span className="font-semibold">{formatCurrency(quote.price)}</span>
              <span className={`text-sm font-medium ${pnlColor(quote.change)}`}>
                {quote.change >= 0 ? "+" : ""}
                {formatCurrency(quote.change)} ({formatPct(quote.change_pct)})
              </span>
            </>
          )}
        </div>

        {/* Period selector */}
        <div className="flex gap-1">
          {PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                period === p
                  ? "bg-secondary text-foreground"
                  : "text-muted-foreground hover:bg-secondary/50"
              }`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      {/* Chart */}
      <div className="rounded-lg border border-border/50 bg-[#0f172a] overflow-hidden">
        {chartLoading && (
          <div className="flex items-center justify-center h-64 text-muted-foreground text-sm">
            Loading chart…
          </div>
        )}
        {!chartLoading && !chartData && (
          <div className="flex items-center justify-center h-64 text-muted-foreground text-sm">
            No data available.
          </div>
        )}
        {chartData && (
          <CandlestickChart
            candles={chartData.candles}
            volumes={chartData.volumes}
            sma50={chartData.sma50}
            sma200={chartData.sma200}
            rsi={chartData.rsi}
            height={450}
          />
        )}
      </div>

      {/* Active signals */}
      {signals.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Active Signals</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {signals.map((s) => {
              const dirColor =
                s.direction === "bullish"
                  ? "text-emerald-500"
                  : s.direction === "bearish"
                  ? "text-red-500"
                  : "text-yellow-500";
              return (
                <div key={s.id} className="rounded-lg border border-border/50 bg-card p-3 text-sm">
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`text-xs font-semibold uppercase ${dirColor}`}>{s.direction}</span>
                    <span className="text-xs text-muted-foreground bg-secondary px-1.5 py-0.5 rounded-full">
                      {s.signal_type}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {"★".repeat(s.strength)}{"☆".repeat(5 - s.strength)}
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground leading-relaxed">{s.rationale}</p>
                  {s.entry_price && (
                    <div className="flex gap-4 text-xs mt-2 pt-2 border-t border-border/50">
                      <span>Entry <strong>{formatCurrency(s.entry_price)}</strong></span>
                      <span className="text-red-400">SL <strong>{formatCurrency(s.stop_loss)}</strong></span>
                      <span className="text-emerald-400">TP <strong>{formatCurrency(s.take_profit)}</strong></span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
