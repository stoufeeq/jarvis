"use client";

/**
 * Compact momentum-score badge for row-level use (watchlist rows,
 * portfolio positions, dashboard tiles).
 *
 * States: strong_bull / bull / neutral / bear / strong_bear, plus
 * a loading placeholder (dash) and a null case (no data — nothing
 * rendered). Hover tooltip shows the ticker's rationale so the user
 * doesn't have to click through unless they want the full breakdown.
 *
 * Pull data via `useMomentumScoresBatch(tickers)` — one HTTP call
 * covers the whole page's rows.
 */

import { useQuery } from "@tanstack/react-query";
import { marketApi, type MomentumScoreResponse, type MomentumInterval } from "@/lib/api";

const VERDICT_STYLE: Record<
  MomentumScoreResponse["verdict"],
  { dot: string; ring: string; text: string; label: string }
> = {
  strong_bull: { dot: "🟢", ring: "border-emerald-500/40 bg-emerald-500/10", text: "text-emerald-400", label: "Strong Bull" },
  bull:        { dot: "🟢", ring: "border-emerald-500/20 bg-emerald-500/5",  text: "text-emerald-400", label: "Bull" },
  neutral:     { dot: "⚪", ring: "border-border/40 bg-secondary/30",        text: "text-muted-foreground", label: "Neutral" },
  bear:        { dot: "🔴", ring: "border-red-500/20 bg-red-500/5",          text: "text-red-400", label: "Bear" },
  strong_bear: { dot: "🔴", ring: "border-red-500/40 bg-red-500/10",         text: "text-red-400", label: "Strong Bear" },
};

interface Props {
  /** The score to render. Pass null while loading; pass undefined for
   *  "no data available for this ticker" (renders nothing). */
  score: MomentumScoreResponse | null | undefined;
  /** Compact = pill-only, no label. Default true for row-level use. */
  compact?: boolean;
}

export function MomentumBadge({ score, compact = true }: Props) {
  if (score === undefined) return null;  // score fetch returned null → nothing
  if (score === null) {
    return (
      <span
        className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded border border-border/30 bg-secondary/20 text-muted-foreground"
        title="Loading momentum score…"
      >
        <span className="opacity-50">…</span>
      </span>
    );
  }
  const style = VERDICT_STYLE[score.verdict];
  return (
    <span
      className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border ${style.ring} ${style.text}`}
      title={`${style.label} · ${score.rationale}`}
    >
      <span className="text-[10px]">{style.dot}</span>
      {!compact && <span className="font-medium">{style.label}</span>}
    </span>
  );
}

/**
 * Batched fetch of momentum scores for a page's ticker list.
 * Single HTTP call, 5-min TanStack Query cache client-side (backend
 * also has 5-min Redis cache), returns a lookup map for O(1) row-level
 * access. Deduplicates tickers so passing duplicates is cheap.
 */
export function useMomentumScoresBatch(
  tickers: string[],
  interval: MomentumInterval = "15m",
) {
  const unique = Array.from(new Set(tickers.map((t) => t.toUpperCase()))).sort();
  const key = unique.join(",");
  return useQuery<Record<string, MomentumScoreResponse | null>>({
    queryKey: ["momentum-scores-batch", key, interval],
    queryFn: () =>
      unique.length === 0
        ? Promise.resolve({})
        : marketApi.momentumScoresBatch(unique, interval).then((r) => r.data),
    enabled: unique.length > 0,
    // Match the backend Redis TTL so we don't hammer with re-fetches
    // while the server would serve the same cached value anyway.
    staleTime: 5 * 60_000,
    // Refresh every 5 min while the tab is open — matches TTL.
    refetchInterval: 5 * 60_000,
    refetchOnWindowFocus: false,
  });
}
