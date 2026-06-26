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

interface PerformanceResponse {
  portfolio_id: number;
  currency: string;
  period: string;
  points: Point[];
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
}

export function PortfolioPerformanceChart({ portfolioId, isPrivate }: Props) {
  const [period, setPeriod] = useState("6mo");

  const { data, isLoading, error } = useQuery<PerformanceResponse>({
    queryKey: ["portfolio-performance", portfolioId, period],
    queryFn: () => portfolioApi.performance(portfolioId, period).then((r) => r.data),
    // Equity curve doesn't move intraday for past dates — only today's
    // tail bar changes. 10 min keeps it cheap and recent.
    staleTime: 10 * 60 * 1000,
  });

  const points = data?.points ?? [];
  const currency = data?.currency ?? "USD";

  // Pick a tick interval that yields roughly 6 visible date labels.
  const tickInterval = points.length > 6 ? Math.floor(points.length / 6) : 0;

  const first = points[0];
  const last = points[points.length - 1];
  const totalReturn =
    first && last && first.market_value > 0
      ? ((last.market_value - first.market_value) / first.market_value) * 100
      : null;
  const absChange = first && last ? last.market_value - first.market_value : null;

  return (
    <div className="rounded-xl border border-border/50 bg-card p-4 space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
            Performance
          </h3>
          {totalReturn != null && absChange != null && (
            <div className="flex items-baseline gap-3 mt-1">
              <span
                className={`text-lg font-bold ${
                  totalReturn >= 0 ? "text-emerald-500" : "text-red-500"
                }`}
              >
                {totalReturn >= 0 ? "+" : ""}
                {totalReturn.toFixed(2)}%
              </span>
              {!isPrivate && (
                <span className="text-sm text-muted-foreground">
                  ({absChange >= 0 ? "+" : ""}
                  {formatCurrency(absChange, currency)} over period)
                </span>
              )}
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
