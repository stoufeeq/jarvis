"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import { portfolioApi } from "@/lib/api";
import { formatCurrency } from "@/lib/utils";

interface Point {
  date: string;
  market_value: number;
  cost_basis: number;
}

interface PerformanceMetrics {
  unrealised_pnl: number;
  unrealised_pnl_pct: number;
  realised_pnl_period: number;
  realised_pnl_all_time: number;
  total_ever_invested: number;
  total_pnl_inception: number;
  total_return_pct_inception: number;
}

interface PerformanceResponse {
  portfolio_id: number;
  currency: string;
  period: string;
  points: Point[];
  metrics?: PerformanceMetrics;
}

const PERIODS: { label: string; value: string }[] = [
  { label: "1M", value: "1mo" },
  { label: "3M", value: "3mo" },
  { label: "6M", value: "6mo" },
  { label: "1Y", value: "1y" },
  { label: "2Y", value: "2y" },
  { label: "5Y", value: "5y" },
  { label: "All", value: "max" },
];

interface Props {
  portfolioId: number;
  isPrivate?: boolean;
  // Currency the parent Portfolio page is displaying totals in (via the
  // top-right CurrencySwitcher). Defaults preserve legacy call sites.
  displayCurrency?: string;
  // Converts a value from the portfolio base currency to displayCurrency.
  // Returns null when the amount itself is null/undefined.
  convert?: (v: number | null | undefined) => number | null;
}

export function PortfolioPerformanceChart({
  portfolioId,
  isPrivate,
  displayCurrency,
  convert,
}: Props) {
  const [period, setPeriod] = useState("6mo");

  const { data, isLoading, error } = useQuery<PerformanceResponse>({
    queryKey: ["portfolio-performance", portfolioId, period],
    queryFn: () => portfolioApi.performance(portfolioId, period).then((r) => r.data),
    // Equity curve doesn't move intraday for past dates — only today's
    // tail bar changes. 10 min keeps it cheap and recent.
    staleTime: 10 * 60 * 1000,
  });

  // The API returns everything in the portfolio's base currency. Convert
  // for display if the parent passed a converter; otherwise show raw
  // (backwards compat with any call site that hasn't wired currency yet).
  const baseCurrency = data?.currency ?? "USD";
  const currency = displayCurrency ?? baseCurrency;
  const conv = (v: number | null | undefined): number => {
    if (v == null) return 0;
    if (!convert) return v;
    const out = convert(v);
    return out ?? v;
  };

  // Convert chart-point mv/cb so the Y-axis + tooltip render in the
  // display currency. Percent metrics are currency-independent — no
  // conversion needed on those.
  const points = (data?.points ?? []).map((p) => ({
    date: p.date,
    market_value: conv(p.market_value),
    cost_basis: conv(p.cost_basis),
  }));

  // Pick a tick interval that yields roughly 6 visible date labels.
  const tickInterval = points.length > 6 ? Math.floor(points.length / 6) : 0;

  const metrics = data?.metrics;

  // Helper to render a coloured signed number/percent pair.
  const signColour = (v: number) =>
    v > 0 ? "text-emerald-500" : v < 0 ? "text-red-500" : "text-muted-foreground";
  const withSign = (v: number, decimals = 2) =>
    (v >= 0 ? "+" : "") + v.toFixed(decimals);

  return (
    <div className="rounded-xl border border-border/50 bg-card p-4 space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
            Performance
          </h3>
          {metrics && (
            <div className="mt-2 grid grid-cols-1 sm:grid-cols-3 gap-x-6 gap-y-1 text-sm">
              {/* 1. Unrealised — current mv−cb for still-open positions */}
              <div>
                <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                  Unrealised
                </p>
                <p className={`font-semibold ${signColour(metrics.unrealised_pnl_pct)}`}>
                  {withSign(metrics.unrealised_pnl_pct)}%
                  {!isPrivate && (
                    <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                      ({withSign(conv(metrics.unrealised_pnl), 0)} {currency})
                    </span>
                  )}
                </p>
                <p className="text-[10px] text-muted-foreground">on held positions</p>
              </div>

              {/* 2. Realised in this period only — sells' P&L */}
              <div>
                <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                  Realised ({period})
                </p>
                <p className={`font-semibold ${signColour(metrics.realised_pnl_period)}`}>
                  {!isPrivate
                    ? `${metrics.realised_pnl_period >= 0 ? "+" : ""}${formatCurrency(
                        conv(metrics.realised_pnl_period),
                        currency,
                      )}`
                    : "•••"}
                </p>
                <p className="text-[10px] text-muted-foreground">from sells in this window</p>
              </div>

              {/* 3. Total return since inception — the honest % */}
              <div>
                <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                  Total return
                </p>
                <p
                  className={`font-semibold ${signColour(metrics.total_return_pct_inception)}`}
                  title={
                    !isPrivate
                      ? `${withSign(conv(metrics.total_pnl_inception), 0)} ${currency} on ${
                          conv(metrics.total_ever_invested).toFixed(0)
                        } ${currency} ever invested`
                      : undefined
                  }
                >
                  {withSign(metrics.total_return_pct_inception)}%
                  {!isPrivate && (
                    <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                      ({withSign(conv(metrics.total_pnl_inception), 0)} {currency})
                    </span>
                  )}
                </p>
                <p className="text-[10px] text-muted-foreground">
                  realised + unrealised ÷ every $ ever bought with
                </p>
              </div>
            </div>
          )}
        </div>
        <div className="flex gap-1">
          {PERIODS.map((p) => (
            <button
              key={p.value}
              onClick={() => setPeriod(p.value)}
              className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                period === p.value
                  ? "bg-secondary text-foreground"
                  : "text-muted-foreground hover:bg-secondary/50"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      <div className="h-72">
        {isLoading && (
          <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
            Loading…
          </div>
        )}
        {!isLoading && error && (
          <div className="flex items-center justify-center h-full text-sm text-red-400">
            Failed to load performance data.
          </div>
        )}
        {!isLoading && !error && points.length === 0 && (
          <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
            No trades yet — add a trade to start tracking performance.
          </div>
        )}
        {!isLoading && !error && points.length > 0 && (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={points} margin={{ top: 10, right: 16, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="mvFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#10b981" stopOpacity={0.35} />
                  <stop offset="100%" stopColor="#10b981" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.15)" />
              <XAxis
                dataKey="date"
                tick={{ fill: "#94a3b8", fontSize: 11 }}
                interval={tickInterval}
                tickFormatter={(d: string) => d.slice(5)}
              />
              <YAxis
                tick={{ fill: "#94a3b8", fontSize: 11 }}
                tickFormatter={(v: number) =>
                  isPrivate ? "•••" : new Intl.NumberFormat("en-US", { notation: "compact" }).format(v)
                }
                width={60}
              />
              <Tooltip
                contentStyle={{
                  background: "rgba(15,23,42,0.95)",
                  border: "1px solid rgba(148,163,184,0.2)",
                  borderRadius: 8,
                  fontSize: 12,
                }}
                labelStyle={{ color: "#94a3b8" }}
                formatter={(value: number, name: string) => [
                  isPrivate ? "•••" : formatCurrency(value, currency),
                  name === "market_value" ? "Market Value" : "Cost Basis",
                ]}
              />
              <Legend
                wrapperStyle={{ fontSize: 12, paddingTop: 4 }}
                formatter={(v: string) => (v === "market_value" ? "Market Value" : "Cost Basis")}
              />
              <Area
                type="monotone"
                dataKey="market_value"
                stroke="#10b981"
                strokeWidth={2}
                fill="url(#mvFill)"
              />
              <Area
                type="monotone"
                dataKey="cost_basis"
                stroke="#94a3b8"
                strokeWidth={1.5}
                strokeDasharray="4 4"
                fill="none"
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
