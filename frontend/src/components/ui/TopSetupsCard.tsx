"use client";

/**
 * Dashboard "Top Setups" tile.
 *
 * Shows the top 3 bullish and top 3 bearish momentum setups across
 * the user's portfolio holdings + watchlist tickers, ranked by score
 * strength then |price − VWAP| (server-side sort in /market/top-setups).
 *
 * One HTTP call, 5-min cache. Rows are clickable — clicking a ticker
 * takes you to its /explore page.
 */

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { TrendingUp, TrendingDown } from "lucide-react";
import { marketApi, type MomentumScoreResponse, type TopSetupsResponse } from "@/lib/api";

function formatRelative(iso: string | null): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  const s = Math.floor((Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

function SetupRow({ score, tone }: { score: MomentumScoreResponse; tone: "bull" | "bear" }) {
  const badge =
    score.verdict === "strong_bull" ? "🟢" :
    score.verdict === "bull" ? "🟢" :
    score.verdict === "strong_bear" ? "🔴" :
    score.verdict === "bear" ? "🔴" : "⚪";
  const label = score.verdict.replace("_", " ");
  const priceDelta =
    score.price != null && score.vwap != null && score.vwap > 0
      ? ((score.price - score.vwap) / score.vwap) * 100
      : null;
  return (
    <Link
      href={`/explore/${score.ticker}`}
      className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-secondary/40 transition-colors"
    >
      <span className="text-sm">{badge}</span>
      <span className="font-semibold text-sm w-16 tabular-nums">{score.ticker}</span>
      <span className={`text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded ${
        tone === "bull"
          ? "bg-emerald-500/15 text-emerald-400"
          : "bg-red-500/15 text-red-400"
      }`}>
        {label}
      </span>
      <span className="flex-1 text-[11px] text-muted-foreground truncate">
        {score.rationale}
      </span>
      {priceDelta != null && (
        <span className={`text-[11px] tabular-nums shrink-0 ${
          priceDelta >= 0 ? "text-emerald-400" : "text-red-400"
        }`}>
          {priceDelta >= 0 ? "+" : ""}{priceDelta.toFixed(2)}% vs VWAP
        </span>
      )}
    </Link>
  );
}

export function TopSetupsCard() {
  const { data, isLoading, isError } = useQuery<TopSetupsResponse>({
    queryKey: ["top-setups", "15m", 3],
    queryFn: () => marketApi.topSetups(3, "15m").then((r) => r.data),
    // Aligned with backend Redis TTL — no reason to poll faster.
    staleTime: 5 * 60_000,
    refetchInterval: 5 * 60_000,
    refetchOnWindowFocus: false,
    retry: 1,
  });

  return (
    <section className="rounded-xl border border-border bg-card p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Top Setups</h2>
        <span className="text-[11px] text-muted-foreground">
          {data?.as_of ? `updated ${formatRelative(data.as_of)}` : ""}
          {data?.universe_size ? ` · scanned ${data.universe_size}` : ""}
        </span>
      </div>

      {isLoading && (
        <p className="text-sm text-muted-foreground">Scanning portfolio + watchlist…</p>
      )}
      {isError && (
        <p className="text-sm text-red-400">Couldn&apos;t load setups. Intraday data may be unavailable outside market hours.</p>
      )}

      {data && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Bulls */}
          <div>
            <div className="flex items-center gap-1.5 mb-1.5 text-xs font-semibold text-emerald-400 uppercase tracking-wider">
              <TrendingUp className="h-3.5 w-3.5" />
              Bulls
            </div>
            {data.bulls.length === 0 ? (
              <p className="text-xs text-muted-foreground italic px-2 py-1.5">No bullish setups in play right now.</p>
            ) : (
              <div className="space-y-0.5">
                {data.bulls.map((s) => (
                  <SetupRow key={s.ticker} score={s} tone="bull" />
                ))}
              </div>
            )}
          </div>

          {/* Bears */}
          <div>
            <div className="flex items-center gap-1.5 mb-1.5 text-xs font-semibold text-red-400 uppercase tracking-wider">
              <TrendingDown className="h-3.5 w-3.5" />
              Bears
            </div>
            {data.bears.length === 0 ? (
              <p className="text-xs text-muted-foreground italic px-2 py-1.5">No bearish setups in play right now.</p>
            ) : (
              <div className="space-y-0.5">
                {data.bears.map((s) => (
                  <SetupRow key={s.ticker} score={s} tone="bear" />
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      <p className="text-[11px] text-muted-foreground/70 pt-1 border-t border-border/40">
        Ranked from your holdings + watchlist by 9/20/50 EMA + VWAP composite (15m).
        Directional context — not a trade signal (backtest showed no automated edge on this basket after costs).
      </p>
    </section>
  );
}
