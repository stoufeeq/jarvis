"use client";

/**
 * Warns about tickers the market-data provider cannot resolve.
 *
 * Exists because MBGD and SYTA 404'd on every price refresh, signal
 * scan, insider fetch and dividend sync for weeks while the UI said
 * nothing — the affected holdings silently contributed zero everywhere,
 * and one position ended up split across two symbols as a result.
 *
 * Only symbols that failed several consecutive daily checks appear here;
 * the backend deliberately tolerates transient provider 404s, which are
 * common even for large liquid names. So if this banner is showing,
 * the symbol really is wrong.
 */

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { marketApi, type UnresolvableTicker } from "@/lib/api";

function daysAgo(iso: string | null): string | null {
  if (!iso) return null;
  const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (d < 1) return "today";
  if (d === 1) return "yesterday";
  return `${d} days ago`;
}

export function UnresolvableTickersBanner() {
  const { data } = useQuery({
    queryKey: ["ticker-health"],
    queryFn: () => marketApi.tickerHealth().then((r) => r.data),
    // Backed by a once-daily check — polling harder tells you nothing new.
    staleTime: 60 * 60 * 1000,
    retry: 1,
  });

  const broken = data?.unresolvable ?? [];
  if (broken.length === 0) return null;

  return (
    <div className="rounded-xl border border-amber-500/40 bg-amber-500/[0.07] p-4 space-y-2">
      <div className="flex items-start gap-2.5">
        <AlertTriangle className="w-4 h-4 text-amber-500 shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-amber-400">
            {broken.length} ticker{broken.length === 1 ? "" : "s"} can&apos;t be found
            by the market data provider
          </p>
          <p className="text-xs text-muted-foreground mt-0.5">
            These contribute nothing to prices, P&amp;L, signals or dividends.
            Their holdings are effectively invisible until the symbol is corrected.
          </p>
        </div>
      </div>

      <div className="space-y-1.5 pl-6">
        {broken.map((t: UnresolvableTicker) => (
          <div key={t.ticker} className="flex items-baseline gap-2 text-xs flex-wrap">
            <span className="font-semibold tabular-nums text-amber-300">{t.ticker}</span>
            <span className="text-muted-foreground">
              {t.never_resolved
                ? "never resolved — likely a wrong symbol"
                : `last worked ${daysAgo(t.last_ok_at) ?? "a while ago"} — likely delisted or renamed`}
            </span>
            <span className="text-muted-foreground/60">
              ({t.consecutive_failures} failed checks)
            </span>
          </div>
        ))}
      </div>

      <p className="text-[11px] text-muted-foreground/70 pl-6 pt-1">
        To fix: find the correct symbol on Yahoo Finance, then rename it across
        your trades. On the server:{" "}
        <code className="bg-black/20 px-1 rounded">
          python scripts/rename_ticker.py --from OLD --to NEW
        </code>{" "}
        (dry-run first; add <code className="bg-black/20 px-1 rounded">--apply</code> to commit).
        It rebuilds the position from your trade ledger, which a plain rename cannot do.
        If the symbol changed because of a merger or reverse split, add{" "}
        <code className="bg-black/20 px-1 rounded">--split OLD:NEW@YYYY-MM-DD</code> so
        pre-split trades are rescaled to match the new quotes.
      </p>
    </div>
  );
}
