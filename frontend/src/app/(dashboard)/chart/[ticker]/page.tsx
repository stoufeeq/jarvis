"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { marketApi, signalsApi } from "@/lib/api";
import { CandlestickChart } from "@/components/charts/CandlestickChart";
import { useChartData } from "@/hooks/useChartData";
import { formatCurrency, formatPct, pnlColor } from "@/lib/utils";
import type { Quote, Signal } from "@/types";

const PERIODS = ["1W", "1M", "3M", "6M", "1Y", "2Y", "5Y"];

export default function ChartPage() {
  const { ticker } = useParams<{ ticker: string }>();
  const [period, setPeriod] = useState("3M");

  const { data: quote } = useQuery<Quote>({
    queryKey: ["quote", ticker],
    queryFn: () => marketApi.quote(ticker).then((r) => r.data),
    refetchInterval: 30_000,
  });

  const { data: chartData, isLoading: chartLoading } = useChartData(ticker, period);

  const { data: signals = [] } = useQuery<Signal[]>({
    queryKey: ["signals", ticker],
    queryFn: () =>
      signalsApi.list({ ticker, limit: 10 }).then((r) => r.data),
  });

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-3xl font-bold">{ticker}</h1>
          {quote && (
            <div className="flex items-baseline gap-3 mt-1">
              <span className="text-2xl font-semibold">
                {formatCurrency(quote.price)}
              </span>
              <span className={`text-lg font-medium ${pnlColor(quote.change)}`}>
                {quote.change >= 0 ? "+" : ""}
                {formatCurrency(quote.change)} ({formatPct(quote.change_pct)})
              </span>
            </div>
          )}
        </div>

        {quote && (
          <div className="flex gap-6 text-sm">
            <Stat label="52W High" value={formatCurrency(quote.fifty_two_week_high)} />
            <Stat label="52W Low" value={formatCurrency(quote.fifty_two_week_low)} />
            {quote.market_cap && (
              <Stat
                label="Market Cap"
                value={`$${(quote.market_cap / 1e9).toFixed(1)}B`}
              />
            )}
          </div>
        )}
      </div>

      {/* Period selector */}
      <div className="flex gap-1">
        {PERIODS.map((p) => (
          <button
            key={p}
            onClick={() => setPeriod(p)}
            className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
              period === p
                ? "bg-secondary text-foreground"
                : "text-muted-foreground hover:bg-secondary/50"
            }`}
          >
            {p}
          </button>
        ))}
      </div>

      {/* Chart */}
      <div className="rounded-xl border border-border bg-card p-4">
        {chartLoading && (
          <div className="flex items-center justify-center h-[460px] text-muted-foreground text-sm">
            Loading chart…
          </div>
        )}
        {!chartLoading && !chartData && (
          <div className="flex items-center justify-center h-[460px] text-muted-foreground text-sm">
            No data available for this period.
          </div>
        )}
        {chartData && (
          <CandlestickChart
            candles={chartData.candles}
            volumes={chartData.volumes}
            sma50={chartData.sma50}
            sma200={chartData.sma200}
            rsi={chartData.rsi}
          />
        )}
      </div>

      {/* Active signals for this ticker */}
      {signals.length > 0 && (
        <div className="space-y-2">
          <h2 className="text-lg font-semibold">Active Signals</h2>
          {signals.map((s) => {
            const dirColor =
              s.direction === "bullish"
                ? "text-emerald-500"
                : s.direction === "bearish"
                ? "text-red-500"
                : "text-yellow-500";
            return (
              <div key={s.id} className="rounded-xl border border-border bg-card p-4">
                <div className="flex items-center gap-2 mb-1">
                  <span className={`text-sm font-semibold uppercase ${dirColor}`}>
                    {s.direction}
                  </span>
                  <span className="text-xs text-muted-foreground bg-secondary px-2 py-0.5 rounded-full">
                    {s.signal_type}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {"★".repeat(s.strength)}{"☆".repeat(5 - s.strength)}
                  </span>
                </div>
                <p className="text-sm text-muted-foreground">{s.rationale}</p>
                {s.entry_price && (
                  <div className="flex gap-6 text-xs mt-2 pt-2 border-t border-border/50">
                    <span>Entry <strong>{formatCurrency(s.entry_price)}</strong></span>
                    <span className="text-red-400">SL <strong>{formatCurrency(s.stop_loss)}</strong></span>
                    <span className="text-emerald-400">TP <strong>{formatCurrency(s.take_profit)}</strong></span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="font-semibold">{value}</p>
    </div>
  );
}
