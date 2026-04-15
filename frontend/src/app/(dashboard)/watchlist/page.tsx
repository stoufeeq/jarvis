"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { watchlistApi, marketApi, signalsApi } from "@/lib/api";
import { TickerSearch } from "@/components/ui/TickerSearch";
import { InlineChart } from "@/components/charts/InlineChart";
import { formatCurrency, pnlColor } from "@/lib/utils";
import type { Quote, WatchlistItem } from "@/types";
import toast from "react-hot-toast";

export default function WatchlistPage() {
  const qc = useQueryClient();
  const [newTicker, setNewTicker] = useState("");
  const [scanning, setScanning] = useState<string | null>(null);
  const [sort, setSort] = useState<{ col: string; dir: "asc" | "desc" }>({ col: "ticker", dir: "asc" });
  const [expandedTicker, setExpandedTicker] = useState<string | null>(null);

  function toggleSort(col: string) {
    setSort((s) => ({ col, dir: s.col === col && s.dir === "asc" ? "desc" : "asc" }));
  }

  function sortIcon(col: string) {
    if (sort.col !== col) return <span className="opacity-30">↕</span>;
    return sort.dir === "asc" ? "↑" : "↓";
  }

  const { data: watchlists = [], isLoading } = useQuery({
    queryKey: ["watchlists"],
    queryFn: () => watchlistApi.list().then((r) => r.data),
  });

  const watchlist = watchlists[0];
  const items: WatchlistItem[] = watchlist?.items ?? [];
  const tickers = items.map((i) => i.ticker);

  // Live quotes overlay — fetched every 30 s, but not required for display
  const { data: liveQuotes = [] } = useQuery<Quote[]>({
    queryKey: ["quotes", tickers],
    queryFn: () =>
      tickers.length > 0
        ? marketApi.quotes(tickers).then((r) => r.data)
        : Promise.resolve([]),
    enabled: tickers.length > 0,
    refetchInterval: 30_000,
  });

  const liveMap = Object.fromEntries(liveQuotes.map((q) => [q.ticker, q]));

  // Merge: live quote takes priority; fall back to DB-cached fields on the item
  function getQuote(item: WatchlistItem): Quote | null {
    const live = liveMap[item.ticker];
    if (live) return live;
    if (item.last_price == null) return null;
    return {
      ticker: item.ticker,
      price: item.last_price,
      previous_close: item.previous_close ?? item.last_price,
      change: item.last_change ?? 0,
      change_pct: item.last_change_pct ?? 0,
      volume: 0,
      market_cap: null,
      fifty_two_week_high: item.fifty_two_week_high ?? item.last_price,
      fifty_two_week_low: item.fifty_two_week_low ?? item.last_price,
    };
  }

  const createWatchlist = useMutation({
    mutationFn: () => watchlistApi.create("Main"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlists"] }),
  });

  const addMutation = useMutation({
    mutationFn: (ticker: string) => watchlistApi.addItem(watchlist.id, ticker),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["watchlists"] });
      setNewTicker("");
      toast.success("Added to watchlist");
    },
    onError: () => toast.error("Ticker already in watchlist"),
  });

  const removeMutation = useMutation({
    mutationFn: (ticker: string) => watchlistApi.removeItem(watchlist.id, ticker),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["watchlists"] });
      toast.success("Removed from watchlist");
    },
  });

  const handleScan = async (ticker: string) => {
    setScanning(ticker);
    try {
      const res = await signalsApi.scan(ticker);
      const count = res.data.length;
      toast.success(count > 0 ? `${count} signal(s) found` : "No signals right now");
    } catch {
      toast.error("Scan failed");
    } finally {
      setScanning(null);
    }
  };

  const handleAdd = (overrideTicker?: string) => {
    const t = (overrideTicker ?? newTicker).trim().toUpperCase();
    if (!t) return;
    if (!watchlist) {
      createWatchlist.mutate(undefined, {
        onSuccess: () => addMutation.mutate(t),
      });
    } else {
      addMutation.mutate(t);
    }
  };

  const handleTickerClick = (ticker: string) => {
    setExpandedTicker((prev) => (prev === ticker ? null : ticker));
  };

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Watchlist</h1>

      {/* Add ticker */}
      <div className="flex gap-2">
        <TickerSearch
          value={newTicker}
          onChange={setNewTicker}
          onSelect={handleAdd}
          placeholder="Search ticker or company name…"
          className="flex-1 sm:flex-none sm:w-72"
        />
        <button
          onClick={() => handleAdd()}
          disabled={!newTicker.trim() || addMutation.isPending}
          className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50"
        >
          Add
        </button>
      </div>

      {isLoading && <p className="text-muted-foreground text-sm">Loading…</p>}

      {items.length === 0 && !isLoading && (
        <div className="rounded-xl border border-dashed border-border p-10 text-center text-muted-foreground text-sm">
          Your watchlist is empty. Add tickers above to track prices and get signals.
        </div>
      )}

      {/* Watchlist table */}
      {items.length > 0 && (
        <div className="rounded-xl border border-border overflow-hidden overflow-x-auto">
          <table className="w-full text-sm min-w-[540px]">
            <thead>
              <tr className="border-b border-border bg-secondary/50">
                {[
                  { col: "ticker", label: "Ticker", align: "left" },
                  { col: "price", label: "Price", align: "right" },
                  { col: "change_pct", label: "Change", align: "right" },
                  { col: "fifty_two_week_high", label: "52W High", align: "right" },
                  { col: "fifty_two_week_low", label: "52W Low", align: "right" },
                ].map(({ col, label, align }) => (
                  <th
                    key={col}
                    onClick={() => toggleSort(col)}
                    className={`px-4 py-3 font-medium text-muted-foreground cursor-pointer select-none hover:text-foreground text-${align}`}
                  >
                    {label} {sortIcon(col)}
                  </th>
                ))}
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {[...items]
                .sort((a, b) => {
                  const qa = getQuote(a);
                  const qb = getQuote(b);
                  let av: string | number;
                  let bv: string | number;
                  if (sort.col === "ticker") {
                    av = a.ticker; bv = b.ticker;
                  } else {
                    av = (qa as unknown as Record<string, number> | null)?.[sort.col] ?? -Infinity;
                    bv = (qb as unknown as Record<string, number> | null)?.[sort.col] ?? -Infinity;
                  }
                  const cmp = typeof av === "string" ? av.localeCompare(bv as string) : (av as number) - (bv as number);
                  return sort.dir === "asc" ? cmp : -cmp;
                })
                .flatMap((item: WatchlistItem) => {
                  const ticker = item.ticker;
                  const q = getQuote(item);
                  const isLive = !!liveMap[ticker];
                  const isStale = !isLive && item.price_updated_at != null;
                  const isExpanded = expandedTicker === ticker;
                  return [
                    <tr key={ticker} className={`border-b border-border/50 hover:bg-secondary/20 ${isExpanded ? "bg-secondary/10" : ""}`}>
                      <td className="px-4 py-3 font-semibold">
                        <button
                          onClick={() => handleTickerClick(ticker)}
                          className="flex items-center gap-1.5 hover:text-primary transition-colors"
                        >
                          <span className="text-xs text-muted-foreground/50 transition-transform duration-200" style={{ transform: isExpanded ? "rotate(90deg)" : "rotate(0deg)" }}>
                            ▶
                          </span>
                          {ticker}
                        </button>
                      </td>
                      <td className="px-4 py-3 text-right">
                        {q ? (
                          <span title={isStale && item.price_updated_at ? `Last updated: ${new Date(item.price_updated_at).toLocaleString()}` : undefined}>
                            {formatCurrency(q.price)}
                            {isStale && <span className="ml-1 text-xs text-muted-foreground/50">●</span>}
                          </span>
                        ) : "—"}
                      </td>
                      <td className={`px-4 py-3 text-right ${q ? pnlColor(q.change) : ""}`}>
                        {q ? `${q.change >= 0 ? "+" : ""}${q.change_pct.toFixed(2)}%` : "—"}
                      </td>
                      <td className="px-4 py-3 text-right text-muted-foreground">
                        {q ? formatCurrency(q.fifty_two_week_high) : "—"}
                      </td>
                      <td className="px-4 py-3 text-right text-muted-foreground">
                        {q ? formatCurrency(q.fifty_two_week_low) : "—"}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <div className="flex gap-2 justify-end">
                          <button
                            onClick={() => handleScan(ticker)}
                            disabled={scanning === ticker}
                            className="text-xs px-2 py-1 rounded bg-secondary hover:bg-secondary/80 disabled:opacity-50"
                          >
                            {scanning === ticker ? "…" : "Scan"}
                          </button>
                          <button
                            onClick={() => removeMutation.mutate(ticker)}
                            className="text-xs px-2 py-1 rounded bg-destructive/20 text-red-400 hover:bg-destructive/40"
                          >
                            Remove
                          </button>
                        </div>
                      </td>
                    </tr>,
                    isExpanded && (
                      <tr key={`${ticker}-chart`}>
                        <td colSpan={6} className="p-0 border-b border-border">
                          <InlineChart ticker={ticker} quote={q ?? undefined} />
                        </td>
                      </tr>
                    ),
                  ].filter(Boolean);
                })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

