"use client";

/**
 * Screener — systematic whole-market rankings.
 *
 * Currently one screen: Greenblatt's Magic Formula (ROCE + EBIT/EV,
 * ranked separately, ranks summed, lowest wins).
 *
 * Both component ranks are shown deliberately. A stock at #1 combined
 * might be 3rd on quality and 40th on cheapness, which is a materially
 * different bet from one that is 20th on both — and the formula's
 * defining behaviour is that those can score identically.
 *
 * Data comes from a weekly Celery job. The compute needs three yfinance
 * calls per ticker across ~500 names, so a live recompute is a queued
 * background job, not something the page waits on.
 */

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { RefreshCw, Info } from "lucide-react";
import toast from "react-hot-toast";
import { screenerApi, type MagicFormulaScreen } from "@/lib/api";
import { TickerLink } from "@/components/ui/TickerLink";
import { HalalBadge } from "@/components/ui/HalalBadge";
import { useHalalCompliance } from "@/hooks/useHalalCompliance";

const LIMITS = [20, 30, 50, 100];

function ageLabel(iso: string | null): string {
  if (!iso) return "never computed";
  const h = Math.floor((Date.now() - new Date(iso).getTime()) / 3_600_000);
  if (h < 1) return "just now";
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export default function ScreenerPage() {
  const [limit, setLimit] = useState(30);

  const { data, isLoading, error } = useQuery<MagicFormulaScreen>({
    queryKey: ["magic-formula", limit],
    queryFn: () => screenerApi.magicFormula(limit).then((r) => r.data),
    // Backed by a weekly job over annual-report figures — polling
    // harder would only re-read the same snapshot.
    staleTime: 60 * 60 * 1000,
  });

  const refresh = useMutation({
    mutationFn: () => screenerApi.refreshMagicFormula(),
    onSuccess: (r) => toast.success((r.data as { detail?: string })?.detail ?? "Refresh queued"),
    onError: () => toast.error("Could not queue refresh"),
  });

  const tickers = (data?.results ?? []).map((r) => r.ticker);
  const halalByTicker = useHalalCompliance(tickers);

  return (
    <div className="space-y-6 max-w-5xl">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold">Screener</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Magic Formula — return on capital crossed with earnings yield
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            className="px-2 py-1.5 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {LIMITS.map((n) => (
              <option key={n} value={n}>Top {n}</option>
            ))}
          </select>
          <button
            onClick={() => refresh.mutate()}
            disabled={refresh.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-secondary text-sm font-medium hover:bg-secondary/80 disabled:opacity-50"
            title="Queue a recompute — takes 15-25 minutes"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${refresh.isPending ? "animate-spin" : ""}`} />
            Recompute
          </button>
        </div>
      </div>

      {/* Method note — this is a strategy with a specific definition and
          known caveats; surfacing them beats letting the table imply
          more authority than it has. */}
      <details className="rounded-lg border border-border/50 bg-card p-3">
        <summary className="text-xs font-medium text-muted-foreground cursor-pointer flex items-center gap-1.5">
          <Info className="w-3.5 h-3.5" />
          How this is calculated, and what to be careful about
        </summary>
        <div className="mt-3 space-y-2 text-xs text-muted-foreground leading-relaxed">
          <p>
            Every eligible stock is ranked twice — once on{" "}
            <strong className="text-foreground">return on capital</strong> (EBIT ÷ capital
            employed) and once on <strong className="text-foreground">earnings yield</strong>{" "}
            (EBIT ÷ enterprise value). The two ranks are added; the lowest total wins.
          </p>
          <p>
            <strong className="text-foreground">Deviation from the book:</strong> Greenblatt used
            return on <em>tangible</em> capital, excluding goodwill. That breakdown isn&apos;t
            reliably available from our data source, so this uses standard ROCE. Acquisitive
            companies carrying large goodwill balances therefore score worse here than in his
            version.
          </p>
          <p>
            <strong className="text-foreground">Worth knowing:</strong> the published backtest
            (30.8%/yr, 1988–2004) has not been matched by independent replications, and
            performance degraded notably after publication. In factor terms this is a value ×
            quality tilt — reasonable, well-documented, and not magic. Value strategies
            underperformed for most of 2010–2020.
          </p>
        </div>
      </details>

      {isLoading && (
        <p className="text-sm text-muted-foreground">Loading screen…</p>
      )}
      {error && (
        <p className="text-sm text-red-400">Failed to load the screen.</p>
      )}

      {data && data.results.length === 0 && (
        <div className="rounded-xl border border-border bg-card p-6 text-sm text-muted-foreground">
          No screen computed yet. It runs weekly (Sunday 02:00 UTC) — or hit{" "}
          <strong>Recompute</strong> to queue one now. Expect 15–25 minutes for ~500 tickers.
        </div>
      )}

      {data && data.results.length > 0 && (
        <>
          {/* Universe accounting — "why isn't JPM here" is the first
              question this screen provokes, so answer it up front. */}
          <div className="rounded-xl border border-border bg-card p-4">
            <div className="flex items-baseline justify-between gap-3 flex-wrap mb-2">
              <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
                Universe
              </h2>
              <span className="text-[11px] text-muted-foreground">
                computed {ageLabel(data.computed_at)}
              </span>
            </div>
            <div className="space-y-1 text-xs">
              <div className="flex justify-between tabular-nums">
                <span className="text-muted-foreground">S&amp;P 500 + your holdings &amp; watchlist</span>
                <span className="font-semibold">{data.universe}</span>
              </div>
              {data.exclusions.map((e) => (
                <div key={e.reason} className="flex justify-between tabular-nums text-muted-foreground">
                  <span>− {e.reason}</span>
                  <span>{e.count}</span>
                </div>
              ))}
              <div className="flex justify-between tabular-nums pt-1 border-t border-border/40 font-semibold">
                <span>Ranked</span>
                <span>{data.ranked}</span>
              </div>
            </div>
          </div>

          <div className="rounded-xl border border-border overflow-hidden overflow-x-auto">
            <table className="w-full text-sm min-w-[720px]">
              <thead>
                <tr className="border-b border-border bg-secondary/50 text-muted-foreground">
                  <th className="px-3 py-2 text-left font-medium w-12">#</th>
                  <th className="px-3 py-2 text-left font-medium">Ticker</th>
                  <th className="px-3 py-2 text-left font-medium">Company</th>
                  <th className="px-3 py-2 text-right font-medium">ROCE</th>
                  <th className="px-3 py-2 text-right font-medium">EV/EBIT</th>
                  <th className="px-3 py-2 text-right font-medium" title="Rank on return on capital">
                    R·cap
                  </th>
                  <th className="px-3 py-2 text-right font-medium" title="Rank on earnings yield">
                    R·yield
                  </th>
                  <th className="px-3 py-2 text-right font-medium">Score</th>
                </tr>
              </thead>
              <tbody>
                {data.results.map((r, i) => (
                  <tr key={r.ticker} className="border-b border-border/50 hover:bg-secondary/20">
                    <td className="px-3 py-2 text-muted-foreground tabular-nums">{i + 1}</td>
                    <td className="px-3 py-2 font-semibold">
                      <div className="flex items-center gap-1.5">
                        <TickerLink ticker={r.ticker} />
                        <HalalBadge compliance={halalByTicker[r.ticker.toUpperCase()]} />
                        {!r.in_sp500 && (
                          <span
                            className="text-[9px] uppercase tracking-wider px-1 py-0.5 rounded bg-blue-500/15 text-blue-400"
                            title="From your holdings or watchlist, not an S&P 500 constituent"
                          >
                            yours
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-muted-foreground truncate max-w-[200px]">
                      {r.company_name ?? "—"}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {r.roce != null ? `${(r.roce * 100).toFixed(1)}%` : "—"}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {r.ev_ebit != null ? `${r.ev_ebit.toFixed(1)}x` : "—"}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                      {r.rank_roce ?? "—"}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                      {r.rank_yield ?? "—"}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums font-semibold">
                      {r.combined_rank ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="text-[11px] text-muted-foreground">
            <strong className="text-foreground">R·cap</strong> and{" "}
            <strong className="text-foreground">R·yield</strong> are the separate ranks —
            a name at 3/40 is a quality bet, one at 40/3 is a value bet, and both can share
            the same score. Greenblatt&apos;s method is to hold roughly 20–30 of these for a
            year, then re-rank.
          </p>
        </>
      )}
    </div>
  );
}
