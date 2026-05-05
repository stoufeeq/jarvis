"use client";

import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { SlidersHorizontal } from "lucide-react";
import { watchlistApi, marketApi, signalsApi } from "@/lib/api";
import { TickerSearch } from "@/components/ui/TickerSearch";
import { InlineChart } from "@/components/charts/InlineChart";
import { formatCurrency, pnlColor } from "@/lib/utils";
import { isCrypto } from "@/lib/crypto";
import type { Quote, WatchlistItem } from "@/types";
import toast from "react-hot-toast";

type ColKey = "change" | "high52" | "low52" | "pe" | "rsi14";

const ALL_COLS: { key: ColKey; label: string }[] = [
  { key: "change", label: "Change" },
  { key: "high52", label: "52W High" },
  { key: "low52", label: "52W Low" },
  { key: "pe", label: "P/E" },
  { key: "rsi14", label: "RSI 14" },
];

const STORAGE_KEY = "jarvis_watchlist_cols";
const DEFAULT_VISIBLE: ColKey[] = ["change", "high52", "low52", "pe", "rsi14"];

function loadVisibleCols(): ColKey[] {
  if (typeof window === "undefined") return DEFAULT_VISIBLE;
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) return JSON.parse(stored) as ColKey[];
  } catch {}
  return DEFAULT_VISIBLE;
}

export default function WatchlistPage() {
  const qc = useQueryClient();
  const [newTicker, setNewTicker] = useState("");
  const [scanning, setScanning] = useState<string | null>(null);
  const [sort, setSort] = useState<{ col: string; dir: "asc" | "desc" }>({ col: "ticker", dir: "asc" });
  const [expandedTicker, setExpandedTicker] = useState<string | null>(null);
  const [visibleCols, setVisibleCols] = useState<ColKey[]>(DEFAULT_VISIBLE);
  const [colPickerOpen, setColPickerOpen] = useState(false);

  // Load persisted column visibility after mount
  useEffect(() => {
    setVisibleCols(loadVisibleCols());
  }, []);

  function toggleCol(key: ColKey) {
    setVisibleCols((prev) => {
      const next = prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key];
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  }

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

  // Live quotes overlay — fetched every 30 s
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

  // RSI colour helper
  function rsiColor(rsi: number | null) {
    if (rsi == null) return "";
    if (rsi >= 70) return "text-red-400";
    if (rsi <= 30) return "text-emerald-400";
    return "text-muted-foreground";
  }

  // Build the visible column definitions in order
  const activeCols = ALL_COLS.filter((c) => visibleCols.includes(c.key));

  // Total cols: Ticker + Price + activeCols + Actions
  const totalColSpan = 2 + activeCols.length + 1;

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
          {/* Column toggle toolbar */}
          <div className="flex items-center justify-end gap-2 px-4 py-2 border-b border-border bg-secondary/30">
            <div className="relative">
              <button
                onClick={() => setColPickerOpen((o) => !o)}
                className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground px-2 py-1 rounded hover:bg-secondary transition-colors"
              >
                <SlidersHorizontal className="w-3.5 h-3.5" />
                Columns
              </button>
              {colPickerOpen && (
                <div className="absolute right-0 top-full mt-1 z-20 bg-card border border-border rounded-lg shadow-lg p-3 min-w-[160px]">
                  <p className="text-xs font-medium text-muted-foreground mb-2">Show / hide columns</p>
                  {ALL_COLS.map((col) => (
                    <label key={col.key} className="flex items-center gap-2 py-1 cursor-pointer group">
                      <input
                        type="checkbox"
                        checked={visibleCols.includes(col.key)}
                        onChange={() => toggleCol(col.key)}
                        className="accent-primary"
                      />
                      <span className="text-sm group-hover:text-foreground text-muted-foreground">
                        {col.label}
                      </span>
                    </label>
                  ))}
                </div>
              )}
            </div>
          </div>

          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-secondary/50">
                <th
                  onClick={() => toggleSort("ticker")}
                  className="px-4 py-3 font-medium text-muted-foreground cursor-pointer select-none hover:text-foreground text-left"
                >
                  Ticker {sortIcon("ticker")}
                </th>
                <th
                  onClick={() => toggleSort("price")}
                  className="px-4 py-3 font-medium text-muted-foreground cursor-pointer select-none hover:text-foreground text-right"
                >
                  Price {sortIcon("price")}
                </th>
                {activeCols.map((col) => (
                  <th
                    key={col.key}
                    onClick={() => toggleSort(col.key)}
                    className="px-4 py-3 font-medium text-muted-foreground cursor-pointer select-none hover:text-foreground text-right"
                  >
                    {col.label} {sortIcon(col.key)}
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
                  } else if (sort.col === "pe") {
                    av = a.pe_ratio ?? -Infinity;
                    bv = b.pe_ratio ?? -Infinity;
                  } else if (sort.col === "rsi14") {
                    av = a.rsi14 ?? -Infinity;
                    bv = b.rsi14 ?? -Infinity;
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
                    <tr
                      key={ticker}
                      className={`border-b border-border/50 hover:bg-secondary/20 ${isExpanded ? "bg-secondary/10" : ""}`}
                    >
                      <td className="px-4 py-3 font-semibold">
                        <button
                          onClick={() => setExpandedTicker((prev) => (prev === ticker ? null : ticker))}
                          className="flex items-center gap-1.5 hover:text-primary transition-colors"
                        >
                          <span
                            className="text-xs text-muted-foreground/50 transition-transform duration-200"
                            style={{ transform: isExpanded ? "rotate(90deg)" : "rotate(0deg)" }}
                          >
                            ▶
                          </span>
                          {ticker}
                          {isCrypto(ticker) && (
                            <span className="ml-1.5 text-[10px] font-medium uppercase tracking-wider px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-500">
                              crypto
                            </span>
                          )}
                        </button>
                      </td>
                      <td className="px-4 py-3 text-right">
                        {q ? (
                          <span
                            title={
                              isStale && item.price_updated_at
                                ? `Last updated: ${new Date(item.price_updated_at).toLocaleString()}`
                                : undefined
                            }
                          >
                            {formatCurrency(q.price)}
                            {isStale && <span className="ml-1 text-xs text-muted-foreground/50">●</span>}
                          </span>
                        ) : "—"}
                      </td>

                      {/* Dynamic columns */}
                      {activeCols.map((col) => {
                        if (col.key === "change") {
                          return (
                            <td key="change" className={`px-4 py-3 text-right ${q ? pnlColor(q.change) : ""}`}>
                              {q ? `${q.change >= 0 ? "+" : ""}${q.change_pct.toFixed(2)}%` : "—"}
                            </td>
                          );
                        }
                        if (col.key === "high52") {
                          return (
                            <td key="high52" className="px-4 py-3 text-right text-muted-foreground">
                              {q ? formatCurrency(q.fifty_two_week_high) : "—"}
                            </td>
                          );
                        }
                        if (col.key === "low52") {
                          return (
                            <td key="low52" className="px-4 py-3 text-right text-muted-foreground">
                              {q ? formatCurrency(q.fifty_two_week_low) : "—"}
                            </td>
                          );
                        }
                        if (col.key === "pe") {
                          return (
                            <td key="pe" className="px-4 py-3 text-right text-muted-foreground">
                              {item.pe_ratio != null ? item.pe_ratio.toFixed(1) : "—"}
                            </td>
                          );
                        }
                        if (col.key === "rsi14") {
                          return (
                            <td key="rsi14" className={`px-4 py-3 text-right font-medium ${rsiColor(item.rsi14)}`}>
                              {item.rsi14 != null ? item.rsi14.toFixed(1) : "—"}
                            </td>
                          );
                        }
                        return null;
                      })}

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
                        <td colSpan={totalColSpan} className="p-0 border-b border-border">
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

      {/* Close col picker when clicking outside */}
      {colPickerOpen && (
        <div className="fixed inset-0 z-10" onClick={() => setColPickerOpen(false)} />
      )}
    </div>
  );
}
