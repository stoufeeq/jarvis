"use client";

import { useState, useMemo } from "react";
import { useQuery, useQueries, useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, TrendingUp, TrendingDown, ChevronLeft, ChevronRight, Plus, Check } from "lucide-react";
import { portfolioApi, signalsApi, accountsApi, marketApi, watchlistApi, briefingApi } from "@/lib/api";
import { formatCurrency, formatPct, pnlColor, currencyLabel } from "@/lib/utils";
import { useCurrencyDisplay } from "@/hooks/useCurrencyDisplay";
import { CurrencySwitcher } from "@/components/ui/CurrencySwitcher";
import { PrivacyToggle } from "@/components/ui/PrivacyToggle";
import { usePrivacyStore } from "@/store/privacy";
import { useTradingModeStore } from "@/store/tradingMode";
import type { Portfolio, Position, Signal, Quote, LiquidityResponse, Briefing } from "@/types";
import Link from "next/link";
import { AnimatedNumber } from "@/components/ui/AnimatedNumber";

const PAGE_SIZE = 7;
const MAX_MOVERS = 49;

const MASK = "••••••";

export default function DashboardPage() {
  const tradingMode = useTradingModeStore((s) => s.mode);
  const isPaper = tradingMode === "paper";

  const { data: allPortfolios = [] } = useQuery<Portfolio[]>({
    queryKey: ["portfolios"],
    queryFn: () => portfolioApi.list().then((r) => r.data),
    staleTime: 60_000,
  });

  // Filter portfolios by current trading mode — never combined.
  const portfolios = useMemo(
    () => allPortfolios.filter((p) =>
      isPaper ? p.broker === "paper" : p.broker !== "paper"
    ),
    [allPortfolios, isPaper]
  );

  // Fetch positions for all active portfolios to recompute live totals
  const portfolioIds = useMemo(
    () => portfolios.filter((p) => p.is_active).map((p) => p.id),
    [portfolios]
  );
  const { data: allPositions = [] } = useQuery<Position[]>({
    queryKey: ["all-positions", portfolioIds],
    queryFn: async () => {
      const results = await Promise.all(
        portfolioIds.map((id) => portfolioApi.positions(id).then((r) => r.data as Position[]))
      );
      return results.flat();
    },
    enabled: portfolioIds.length > 0,
    staleTime: 60_000,
  });

  const positionTickers = useMemo(
    () => [...new Set(allPositions.map((p) => p.ticker))],
    [allPositions]
  );
  const { data: liveQuotes = [] } = useQuery<Quote[]>({
    queryKey: ["dashboard-quotes", positionTickers],
    queryFn: () => marketApi.quotes(positionTickers).then((r) => r.data),
    enabled: positionTickers.length > 0,
    staleTime: 60_000,
    refetchInterval: 60_000,
  });
  const liveQuoteMap = useMemo(
    () => Object.fromEntries(liveQuotes.map((q) => [q.ticker, q])),
    [liveQuotes]
  );

  const { data: signals = [] } = useQuery<Signal[]>({
    queryKey: ["signals", "recent"],
    queryFn: () => signalsApi.list({ limit: 10 }).then((r) => r.data),
    staleTime: 60_000,
  });

  const { data: heatmapData } = useQuery({
    queryKey: ["heatmap"],
    queryFn: () => marketApi.heatmap().then((r) => r.data),
    staleTime: 30 * 60 * 1000,  // 30 min — shared cache with /heatmap page
  });

  const { data: liquidity } = useQuery<LiquidityResponse>({
    queryKey: ["liquidity"],
    queryFn: () => accountsApi.liquidity().then((r) => r.data),
    staleTime: 60_000,
  });

  const { data: briefing } = useQuery<Briefing>({
    queryKey: ["briefing", "today"],
    queryFn: () => briefingApi.today().then((r) => r.data),
    staleTime: 1000 * 60 * 5,
    retry: false,
  });

  const dashboardBase = "USD";
  const { displayCurrency, setDisplayCurrency, rate, convert, base: baseCurrency } =
    useCurrencyDisplay(dashboardBase);

  const isPrivate = usePrivacyStore((s) => s.isPrivate);

  // Collect unique non-USD portfolio currencies so we can normalise P&L to USD
  const nonUsdCurrencies = [...new Set(
    portfolios.map((p) => (p.currency || "USD").toUpperCase()).filter((c) => c !== "USD")
  )];

  const fxQueries = useQueries({
    queries: nonUsdCurrencies.map((ccy) => ({
      queryKey: ["fx", ccy, "USD"],
      queryFn: () => marketApi.fx(ccy, "USD").then((r) => ({ ccy, rate: r.data.rate as number })),
      staleTime: 60_000,
    })),
  });

  // Map of currency → USD rate (e.g. { GBP: 1.27, EUR: 1.08 })
  const fxRates: Record<string, number> = Object.fromEntries(
    fxQueries.filter((q) => q.data).map((q) => [q.data!.ccy, q.data!.rate])
  );

  function toUsd(amount: number, currency: string): number {
    const ccy = (currency || "USD").toUpperCase();
    if (ccy === "USD") return amount;
    const rate = fxRates[ccy];
    return rate ? amount * rate : amount; // keep as-is until rate loads
  }

  // Recompute totals from live quotes when available, fall back to DB-cached
  const liveTotals = useMemo(() => {
    if (!allPositions.length || !liveQuotes.length) return null;
    let totalValue = 0;
    let totalCost = 0;
    let dayChange = 0;
    for (const pos of allPositions) {
      const q = liveQuoteMap[pos.ticker];
      const price = q?.price ?? pos.current_price;
      const cost = pos.avg_cost * pos.quantity;
      totalCost += cost;
      if (price != null) {
        totalValue += price * pos.quantity;
        if (q?.previous_close) {
          dayChange += (price - q.previous_close) * pos.quantity;
        }
      }
    }
    const totalPnl = totalValue - totalCost;
    return { totalValue, totalCost, totalPnl, dayChange };
  }, [allPositions, liveQuotes, liveQuoteMap]);

  const portfolioValue = liveTotals?.totalValue
    ?? portfolios.reduce((s, p) => s + toUsd(p.total_value ?? 0, p.currency || "USD"), 0);
  // In paper mode, "Cash" comes from the paper portfolio's virtual cash_balance,
  // not from real cash accounts.
  const paperCashUsd = isPaper
    ? portfolios.reduce((s, p) => s + toUsd(p.cash_balance ?? 0, p.currency || "USD"), 0)
    : 0;
  const liquidityUsd = isPaper ? paperCashUsd : (liquidity?.total_usd ?? 0);
  const totalValue = portfolioValue + liquidityUsd;
  const totalPnl = liveTotals?.totalPnl
    ?? portfolios.reduce((s, p) => s + toUsd(p.total_pnl ?? 0, p.currency || "USD"), 0);
  const totalDayChange = liveTotals?.dayChange
    ?? portfolios.reduce((s, p) => s + toUsd(p.day_change ?? 0, p.currency || "USD"), 0);
  const prevTotal = portfolioValue - totalDayChange;
  const totalDayChangePct = prevTotal ? (totalDayChange / prevTotal) * 100 : null;

  const mv = (val: string) => (isPrivate ? MASK : val);

  // Flatten all stocks from heatmap sectors, filter out nulls, sort by change
  const allStocks: { ticker: string; name: string; change_pct: number }[] =
    (heatmapData?.sectors ?? [])
      .flatMap((sec: { children: { ticker: string; name: string; change_pct: number | null }[] }) => sec.children)
      .filter((s: { change_pct: number | null }) => s.change_pct != null);

  const allGainers = [...allStocks].sort((a, b) => b.change_pct - a.change_pct).slice(0, MAX_MOVERS);
  const allLosers  = [...allStocks].sort((a, b) => a.change_pct - b.change_pct).slice(0, MAX_MOVERS);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Dashboard</h1>
        <div className="flex items-center gap-2">
          <PrivacyToggle />
          <CurrencySwitcher
            base={baseCurrency}
            display={displayCurrency}
            rate={rate}
            onChange={setDisplayCurrency}
          />
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
        <StatCard
          label="Total Value"
          value={mv(formatCurrency(convert(totalValue), displayCurrency))}
          note={isPrivate ? undefined : currencyLabel(displayCurrency)}
        />
        <StatCard
          label={isPaper ? "Virtual Cash" : "Liquidity"}
          value={mv(formatCurrency(convert(liquidityUsd), displayCurrency))}
          note={isPrivate ? undefined : currencyLabel(displayCurrency)}
          valueClass={isPaper ? "text-amber-500" : "text-sky-400"}
        />
        <StatCard
          label="Unrealized P&L"
          value={mv(formatCurrency(convert(totalPnl), displayCurrency))}
          valueClass={isPrivate ? undefined : pnlColor(totalPnl)}
          note={isPrivate ? undefined : currencyLabel(displayCurrency)}
        />
        <StatCard
          label="Today's Change"
          value={mv(formatCurrency(convert(totalDayChange), displayCurrency))}
          valueClass={isPrivate ? undefined : pnlColor(totalDayChange)}
          note={isPrivate || totalDayChangePct == null ? undefined : formatPct(totalDayChangePct)}
        />
        <StatCard label="Portfolios" value={String(portfolios.length)} />
      </div>

      {/* Daily Briefing card */}
      {briefing && <BriefingCard briefing={briefing} />}

      {/* Top Movers */}
      {allStocks.length > 0 && (
        <TopMovers
          gainers={allGainers}
          losers={allLosers}
          cachedAt={heatmapData?.cached_at}
        />
      )}

      {/* Recent signals */}
      <section>
        <h2 className="text-lg font-semibold mb-3">Recent Signals</h2>
        {signals.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            No signals yet. Add tickers to your watchlist to start scanning.
          </p>
        ) : (
          <div className="space-y-2">
            {signals.map((s) => (
              <SignalRow key={s.id} signal={s} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

const SENTIMENT_PILL: Record<string, { bg: string; text: string; label: string }> = {
  bullish:  { bg: "bg-emerald-500/15", text: "text-emerald-400", label: "Bullish" },
  neutral:  { bg: "bg-slate-500/15",   text: "text-slate-400",   label: "Neutral" },
  cautious: { bg: "bg-amber-500/15",   text: "text-amber-400",   label: "Cautious" },
  bearish:  { bg: "bg-red-500/15",     text: "text-red-400",     label: "Bearish" },
};

function BriefingCard({ briefing }: { briefing: Briefing }) {
  const pill = SENTIMENT_PILL[briefing.overall_sentiment] ?? SENTIMENT_PILL.neutral;
  const bullets = briefing.summary
    ? briefing.summary.split("\n").filter(Boolean)
    : briefing.content?.summary_bullets?.map((b) => `• ${b}`) ?? [];

  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-foreground">Daily Briefing</h2>
          <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${pill.bg} ${pill.text}`}>
            {pill.label}
          </span>
        </div>
        <Link
          href="/briefing"
          className="text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          Read full briefing →
        </Link>
      </div>
      {bullets.length > 0 ? (
        <ul className="space-y-1.5">
          {bullets.map((b, i) => (
            <li key={i} className="text-sm text-muted-foreground">{b}</li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">Briefing generated. Click to read.</p>
      )}
    </div>
  );
}

function StatCard({
  label, value, valueClass, note,
}: {
  label: string;
  value: string;
  valueClass?: string;
  note?: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-4 min-w-0">
      <div className="flex items-center justify-between gap-1 min-w-0">
        <p className="text-sm text-muted-foreground truncate">{label}</p>
        {note && <span className="text-xs text-muted-foreground/60 shrink-0">{note}</span>}
      </div>
      <p className={`text-lg sm:text-2xl font-bold mt-1 truncate ${valueClass ?? ""}`}>
        <AnimatedNumber value={value} />
      </p>
    </div>
  );
}

function SignalRow({ signal }: { signal: Signal }) {
  const [expanded, setExpanded] = useState(false);

  const dirColor =
    signal.direction === "bullish"
      ? "text-emerald-500"
      : signal.direction === "bearish"
      ? "text-red-500"
      : "text-yellow-500";

  return (
    <div
      className="rounded-lg border border-border bg-card p-3 cursor-pointer select-none"
      onClick={() => setExpanded((v) => !v)}
    >
      {/* Header row — always visible */}
      <div className="flex items-start gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold">{signal.ticker}</span>
            <span className={`text-xs font-medium uppercase ${dirColor}`}>
              {signal.direction}
            </span>
            <span className="text-xs text-muted-foreground">
              {"★".repeat(signal.strength)}{"☆".repeat(5 - signal.strength)}
            </span>
            <span className="text-xs text-muted-foreground">{signal.signal_type.replace(/_/g, " ")}</span>
            {signal.timeframe && (
              <span className="text-xs text-muted-foreground">{signal.timeframe}</span>
            )}
          </div>
          {/* Rationale — truncated when collapsed, full when expanded */}
          {signal.rationale && (
            <p className={`text-sm text-muted-foreground mt-1 ${expanded ? "" : "truncate"}`}>{signal.rationale}</p>
          )}
          {/* Indicators — only when expanded */}
          {expanded && signal.indicators && (
            <p className="text-xs text-muted-foreground/60 font-mono mt-1">{signal.indicators}</p>
          )}
          {/* Entry / SL / TP — always visible */}
          {signal.entry_price && (
            <div className="flex gap-4 text-xs mt-1">
              <div>Entry <span className="font-medium text-foreground">{formatCurrency(signal.entry_price)}</span></div>
              {signal.stop_loss && <div>SL <span className="font-medium text-red-400">{formatCurrency(signal.stop_loss)}</span></div>}
              {signal.take_profit && <div>TP <span className="font-medium text-emerald-400">{formatCurrency(signal.take_profit)}</span></div>}
            </div>
          )}
        </div>
        <ChevronDown
          className={`w-4 h-4 text-muted-foreground shrink-0 mt-0.5 transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
        />
      </div>

    </div>
  );
}

type MoverStock = { ticker: string; name: string; change_pct: number };

function MoverRow({
  rank, stock, inWatchlist, onAdd, adding, color,
}: {
  rank: number;
  stock: MoverStock;
  inWatchlist: boolean;
  onAdd: () => void;
  adding: boolean;
  color: "emerald" | "red";
}) {
  const pctClass = color === "emerald" ? "text-emerald-500" : "text-red-500";
  const pctLabel = color === "emerald"
    ? `+${stock.change_pct.toFixed(2)}%`
    : `${stock.change_pct.toFixed(2)}%`;

  return (
    <div className="flex items-center gap-2 px-3 py-2 hover:bg-secondary/30 transition-colors">
      <span className="text-xs text-muted-foreground/50 w-5 shrink-0 text-right">{rank}</span>
      <div className="flex-1 min-w-0">
        <span className="font-semibold text-sm">{stock.ticker}</span>
        <span className="text-xs text-muted-foreground ml-2 truncate hidden sm:inline">{stock.name}</span>
      </div>
      <span className={`text-sm font-semibold shrink-0 ${pctClass}`}>{pctLabel}</span>
      <button
        onClick={onAdd}
        disabled={inWatchlist || adding}
        title={inWatchlist ? "Already in watchlist" : "Add to watchlist"}
        className={`shrink-0 w-6 h-6 flex items-center justify-center rounded-md transition-colors
          ${inWatchlist
            ? "text-emerald-500 cursor-default"
            : "text-muted-foreground hover:text-foreground hover:bg-secondary disabled:opacity-40"
          }`}
      >
        {inWatchlist ? <Check className="w-3.5 h-3.5" /> : <Plus className="w-3.5 h-3.5" />}
      </button>
    </div>
  );
}

function TopMovers({
  gainers,
  losers,
  cachedAt,
}: {
  gainers: MoverStock[];
  losers: MoverStock[];
  cachedAt?: number;
}) {
  const [page, setPage] = useState(0);
  const qc = useQueryClient();

  const { data: watchlists = [] } = useQuery({
    queryKey: ["watchlists"],
    queryFn: () => watchlistApi.list().then((r) => r.data),
    staleTime: 60_000,
  });
  const watchlist = watchlists[0];
  const watchedTickers = new Set<string>((watchlist?.items ?? []).map((i: { ticker: string }) => i.ticker));

  const addMutation = useMutation({
    mutationFn: (ticker: string) => watchlistApi.addItem(watchlist.id, ticker),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlists"] }),
  });

  const totalPages = Math.ceil(Math.max(gainers.length, losers.length) / PAGE_SIZE);
  const start = page * PAGE_SIZE;
  const end = start + PAGE_SIZE;
  const pageGainers = gainers.slice(start, end);
  const pageLosers  = losers.slice(start, end);

  return (
    <section>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">
          Top Movers{" "}
          <span className="text-xs font-normal text-muted-foreground ml-1">S&amp;P 500 · today</span>
        </h2>
        {/* Pagination controls */}
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">
            {start + 1}–{Math.min(end, Math.max(gainers.length, losers.length))} of {Math.max(gainers.length, losers.length)}
          </span>
          <button
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
            className="p-1 rounded-md hover:bg-secondary disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
          <button
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
            disabled={page >= totalPages - 1}
            className="p-1 rounded-md hover:bg-secondary disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {/* Gainers */}
        <div className="rounded-xl border border-border bg-card overflow-hidden">
          <div className="flex items-center gap-2 px-3 py-2 border-b border-border bg-emerald-500/5">
            <TrendingUp className="w-4 h-4 text-emerald-500" />
            <span className="text-sm font-medium text-emerald-500">Gainers</span>
            <span className="text-xs text-muted-foreground ml-auto">#{start + 1}–{start + pageGainers.length}</span>
          </div>
          <div className="divide-y divide-border">
            {pageGainers.map((s, i) => (
              <MoverRow
                key={s.ticker}
                rank={start + i + 1}
                stock={s}
                inWatchlist={watchedTickers.has(s.ticker)}
                onAdd={() => addMutation.mutate(s.ticker)}
                adding={addMutation.isPending && addMutation.variables === s.ticker}
                color="emerald"
              />
            ))}
          </div>
        </div>

        {/* Losers */}
        <div className="rounded-xl border border-border bg-card overflow-hidden">
          <div className="flex items-center gap-2 px-3 py-2 border-b border-border bg-red-500/5">
            <TrendingDown className="w-4 h-4 text-red-500" />
            <span className="text-sm font-medium text-red-500">Losers</span>
            <span className="text-xs text-muted-foreground ml-auto">#{start + 1}–{start + pageLosers.length}</span>
          </div>
          <div className="divide-y divide-border">
            {pageLosers.map((s, i) => (
              <MoverRow
                key={s.ticker}
                rank={start + i + 1}
                stock={s}
                inWatchlist={watchedTickers.has(s.ticker)}
                onAdd={() => addMutation.mutate(s.ticker)}
                adding={addMutation.isPending && addMutation.variables === s.ticker}
                color="red"
              />
            ))}
          </div>
        </div>
      </div>

      {cachedAt && (
        <p className="text-xs text-muted-foreground/50 mt-1.5 text-right">
          Data as of {new Date(cachedAt * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
        </p>
      )}
    </section>
  );
}
