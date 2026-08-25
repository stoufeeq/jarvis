"use client";

/**
 * 9/20/50 EMA + VWAP momentum score card.
 *
 * Full-width card that sits above the candlestick chart. Shows the
 * overall verdict + per-component breakdown + one-line rationale.
 * Timeframe dropdown lets the user pick 5m / 15m / 1h — VWAP is
 * session-anchored so daily is deliberately not offered (would
 * degenerate to close-price and add no info).
 *
 * Auto-refreshes every 60s while the tab is active.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { marketApi, type MomentumInterval, type MomentumScoreResponse, type MomentumComponent } from "@/lib/api";

const INTERVALS: MomentumInterval[] = ["5m", "15m", "1h"];

const VERDICT_META: Record<
  MomentumScoreResponse["verdict"],
  { label: string; ring: string; text: string; dot: string }
> = {
  strong_bull: { label: "Strong Bull", ring: "border-emerald-500/40 bg-emerald-500/10", text: "text-emerald-400", dot: "🟢" },
  bull:        { label: "Bull",        ring: "border-emerald-500/25 bg-emerald-500/5", text: "text-emerald-400", dot: "🟢" },
  neutral:     { label: "Neutral",     ring: "border-border bg-secondary/30",           text: "text-muted-foreground", dot: "⚪" },
  bear:        { label: "Bear",        ring: "border-red-500/25 bg-red-500/5",         text: "text-red-400", dot: "🔴" },
  strong_bear: { label: "Strong Bear", ring: "border-red-500/40 bg-red-500/10",        text: "text-red-400", dot: "🔴" },
};

function directionIcon(dir: MomentumComponent["direction"]): string {
  return dir === "bullish" ? "✓" : dir === "bearish" ? "✗" : "○";
}

function directionColor(dir: MomentumComponent["direction"]): string {
  return dir === "bullish" ? "text-emerald-400" : dir === "bearish" ? "text-red-400" : "text-muted-foreground";
}

function formatRelative(iso: string): string {
  const t = new Date(iso).getTime();
  const s = Math.floor((Date.now() - t) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

interface Props {
  ticker: string;
}

export function MomentumScoreCard({ ticker }: Props) {
  const [interval, setInterval] = useState<MomentumInterval>("15m");

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery<MomentumScoreResponse>({
    queryKey: ["momentum-score", ticker, interval],
    queryFn: () => marketApi.momentumScore(ticker, interval).then((r) => r.data),
    // 60s refresh — the setup evolves bar-by-bar, no faster
    refetchInterval: 60_000,
    // Refresh on window focus so returning to the tab feels current
    refetchOnWindowFocus: true,
    retry: 1,
  });

  return (
    <section className="rounded-xl border border-border bg-card p-4 sm:p-5 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-baseline gap-2">
          <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
            Momentum Score
          </h2>
          <span className="text-xs text-muted-foreground">· {ticker}</span>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={interval}
            onChange={(e) => setInterval(e.target.value as MomentumInterval)}
            className="px-2 py-1 rounded-md border border-border bg-input text-xs focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {INTERVALS.map((i) => (
              <option key={i} value={i}>{i}</option>
            ))}
          </select>
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="px-2 py-1 rounded-md text-xs text-muted-foreground hover:text-foreground hover:bg-secondary/50 disabled:opacity-50"
            title="Refresh"
          >
            {isFetching ? "…" : "↻"}
          </button>
        </div>
      </div>

      {/* Body */}
      {isLoading && (
        <p className="text-sm text-muted-foreground py-4">Computing…</p>
      )}

      {isError && (
        <p className="text-sm text-red-400 py-4">
          {(error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
            ?? "Could not compute score. Intraday data may be unavailable outside market hours."}
        </p>
      )}

      {data && (
        <>
          {/* Verdict pill + score bar */}
          {(() => {
            const meta = VERDICT_META[data.verdict];
            const percent = Math.round((data.score_abs / 4) * 100);
            return (
              <div className={`rounded-lg border ${meta.ring} p-3 flex items-center gap-4 flex-wrap`}>
                <div className="flex items-baseline gap-2">
                  <span className="text-2xl">{meta.dot}</span>
                  <span className={`text-lg font-semibold ${meta.text}`}>{meta.label}</span>
                </div>
                <div className="flex-1 min-w-[160px]">
                  <div className="flex items-center gap-2">
                    <div className="flex-1 h-2 rounded-full bg-secondary/50 overflow-hidden">
                      <div
                        className={`h-full transition-all ${
                          data.direction === "bullish" ? "bg-emerald-500" :
                          data.direction === "bearish" ? "bg-red-500" : "bg-muted-foreground"
                        }`}
                        style={{ width: `${percent}%` }}
                      />
                    </div>
                    <span className="text-xs text-muted-foreground tabular-nums whitespace-nowrap">
                      {data.score_abs} / 4
                    </span>
                  </div>
                </div>
              </div>
            );
          })()}

          {/* Component breakdown */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {data.components.map((c) => (
              <div
                key={c.key}
                className="flex items-start gap-2 rounded-md border border-border/40 bg-secondary/20 px-3 py-2"
              >
                <span className={`text-sm font-semibold ${directionColor(c.direction)} shrink-0 mt-0.5`}>
                  {directionIcon(c.direction)}
                </span>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{c.label}</p>
                  <p className="text-xs text-muted-foreground truncate">{c.detail}</p>
                </div>
              </div>
            ))}
          </div>

          {/* Rationale */}
          <p className="text-xs text-muted-foreground italic leading-snug">
            &ldquo;{data.rationale}&rdquo;
          </p>

          {/* Freshness footer */}
          <div className="flex items-center justify-between text-[11px] text-muted-foreground/70 pt-1 border-t border-border/40">
            <span>
              updated {formatRelative(data.updated_at)} · auto-refresh 60s
            </span>
            {data.vwap != null && data.price != null && (
              <span className="tabular-nums">
                price {data.price.toFixed(2)} · VWAP {data.vwap.toFixed(2)}
              </span>
            )}
          </div>
        </>
      )}
    </section>
  );
}
