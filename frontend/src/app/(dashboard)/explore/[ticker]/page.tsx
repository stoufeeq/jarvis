"use client";

import { useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Bell, ArrowUp, ArrowDown, Minus } from "lucide-react";
import {
  marketApi,
  signalsApi,
  watchlistApi,
  alertsApi,
} from "@/lib/api";
import { InlineChart } from "@/components/charts/InlineChart";
import { formatCurrency, formatPct, pnlColor } from "@/lib/utils";
import { isCrypto } from "@/lib/crypto";
import type {
  StockDetails,
  Signal,
  AggregatedTicker,
  NewsItemDetail,
  InsiderTradeDetail,
  WatchlistItem,
  Quote,
} from "@/types";

interface Watchlist {
  id: number;
  name: string;
  items: WatchlistItem[];
}
import toast from "react-hot-toast";

export default function ExplorePage() {
  const params = useParams<{ ticker: string }>();
  const ticker = (params?.ticker as string)?.toUpperCase();

  const qc = useQueryClient();

  // Tier 1 — instant: live quote (60s cache, fast endpoint)
  const { data: quote } = useQuery<Quote>({
    queryKey: ["quote", ticker],
    queryFn: () => marketApi.quote(ticker).then((r) => r.data),
    enabled: !!ticker,
    refetchInterval: 60_000,
  });

  // Tier 2 — slow: full details (5-min cache server-side)
  const { data: details, isLoading: detailsLoading } = useQuery<StockDetails>({
    queryKey: ["stock-details", ticker],
    queryFn: () => marketApi.details(ticker).then((r) => r.data),
    enabled: !!ticker,
    staleTime: 5 * 60_000,
  });

  // Tier 3 — separate fetch: existing signals on this ticker
  const { data: signals = [] } = useQuery<Signal[]>({
    queryKey: ["signals", "explore", ticker],
    queryFn: () => signalsApi.list({ ticker, limit: 20 }).then((r) => r.data),
    enabled: !!ticker,
  });

  const { data: aggregated } = useQuery<AggregatedTicker[]>({
    queryKey: ["signals", "aggregated-ticker", ticker],
    queryFn: () =>
      signalsApi.aggregatedByTicker(200)
        .then((r) => (r.data as AggregatedTicker[]).filter((t) => t.ticker === ticker)),
    enabled: !!ticker,
  });

  // News (on-demand fetch via the show button)
  const [newsExpanded, setNewsExpanded] = useState(false);
  const { data: news = [], isFetching: newsFetching } = useQuery<NewsItemDetail[]>({
    queryKey: ["details-news", ticker],
    queryFn: () => marketApi.detailsNews(ticker).then((r) => r.data),
    enabled: !!ticker && newsExpanded,
    staleTime: 60_000,
  });

  // Insider (on-demand)
  const [insiderExpanded, setInsiderExpanded] = useState(false);
  const { data: insider = [], isFetching: insiderFetching } = useQuery<InsiderTradeDetail[]>({
    queryKey: ["details-insider", ticker],
    queryFn: () => marketApi.detailsInsider(ticker).then((r) => r.data),
    enabled: !!ticker && insiderExpanded,
    staleTime: 60_000,
  });

  // Watchlist add
  const { data: watchlists = [] } = useQuery<Watchlist[]>({
    queryKey: ["watchlists"],
    queryFn: () => watchlistApi.list().then((r) => r.data),
  });
  const watchlist = watchlists[0];
  const isInWatchlist = watchlist?.items?.some((w) => w.ticker === ticker);

  const addToWatchlist = useMutation({
    mutationFn: () => watchlistApi.addItem(watchlist!.id, ticker),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["watchlists"] });
      toast.success(`${ticker} added to watchlist`);
    },
    onError: () => toast.error("Failed to add"),
  });

  // Quick alert
  const [showAlertForm, setShowAlertForm] = useState(false);
  const [alertPrice, setAlertPrice] = useState("");
  const [alertType, setAlertType] = useState<"price_above" | "price_below">("price_above");
  const createAlert = useMutation({
    mutationFn: () => alertsApi.create({
      ticker,
      alert_type: alertType,
      threshold_value: parseFloat(alertPrice) || 0,
      channels: "in_app",
    }),
    onSuccess: () => {
      toast.success("Alert created");
      setShowAlertForm(false);
      setAlertPrice("");
      qc.invalidateQueries({ queryKey: ["alerts"] });
    },
    onError: () => toast.error("Failed to create alert"),
  });

  // Run scan
  const scanMutation = useMutation({
    mutationFn: () => signalsApi.scan(ticker),
    onSuccess: (res) => {
      toast.success(res.data.length > 0 ? `Found ${res.data.length} signal(s)` : "No signals fired");
      qc.invalidateQueries({ queryKey: ["signals", "explore", ticker] });
      qc.invalidateQueries({ queryKey: ["signals", "aggregated-ticker", ticker] });
    },
    onError: () => toast.error("Scan failed"),
  });

  if (!ticker) return null;

  const tickerAgg = aggregated && aggregated.length > 0 ? aggregated[0] : null;
  const ccy = details?.currency ?? "USD";
  const isPriceUp = (quote?.change_pct ?? 0) >= 0;

  return (
    <div className="space-y-6 max-w-6xl">
      {/* ── Section 1: Header / Quick Snapshot ─────────────────────────────── */}
      <div className="space-y-3">
        <div className="flex items-start justify-between flex-wrap gap-3">
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-3xl font-bold">{ticker}</h1>
              {isCrypto(ticker) && (
                <span className="text-[10px] uppercase tracking-wider px-2 py-0.5 rounded bg-amber-500/10 text-amber-500">crypto</span>
              )}
              {details?.exchange && (
                <span className="text-xs text-muted-foreground">{details.exchange}</span>
              )}
              {details?.sector && (
                <span className="text-xs text-muted-foreground">· {details.sector}</span>
              )}
            </div>
            <p className="text-sm text-muted-foreground mt-1">{details?.name ?? "Loading…"}</p>
          </div>

          <div className="flex items-center gap-2">
            {watchlist && !isInWatchlist && (
              <button
                onClick={() => addToWatchlist.mutate()}
                disabled={addToWatchlist.isPending}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-secondary text-sm hover:bg-secondary/80 transition-colors disabled:opacity-50"
              >
                <Plus className="w-3.5 h-3.5" /> Watchlist
              </button>
            )}
            {isInWatchlist && (
              <span className="px-3 py-1.5 rounded-md bg-emerald-500/10 text-emerald-500 text-sm">
                ✓ In watchlist
              </span>
            )}
            <button
              onClick={() => setShowAlertForm((v) => !v)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-secondary text-sm hover:bg-secondary/80 transition-colors"
            >
              <Bell className="w-3.5 h-3.5" /> Alert
            </button>
            <button
              onClick={() => scanMutation.mutate()}
              disabled={scanMutation.isPending}
              className="px-3 py-1.5 rounded-md bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50"
            >
              {scanMutation.isPending ? "Scanning…" : "Scan"}
            </button>
          </div>
        </div>

        {/* Alert creation form */}
        {showAlertForm && (
          <div className="rounded-lg border border-border bg-card p-3 flex flex-wrap items-end gap-2">
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Type</label>
              <select
                value={alertType}
                onChange={(e) => setAlertType(e.target.value as "price_above" | "price_below")}
                className="px-2 py-1.5 rounded border border-border bg-input text-sm"
              >
                <option value="price_above">Above</option>
                <option value="price_below">Below</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Price</label>
              <input
                type="number"
                value={alertPrice}
                onChange={(e) => setAlertPrice(e.target.value)}
                className="px-2 py-1.5 rounded border border-border bg-input text-sm w-28"
              />
            </div>
            <button
              onClick={() => createAlert.mutate()}
              disabled={!alertPrice || createAlert.isPending}
              className="px-3 py-1.5 rounded bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50"
            >
              Save alert
            </button>
            <button
              onClick={() => setShowAlertForm(false)}
              className="px-3 py-1.5 rounded text-sm text-muted-foreground"
            >
              Cancel
            </button>
          </div>
        )}

        {/* Price + day change */}
        <div className="flex items-baseline gap-3 flex-wrap">
          <span className="text-4xl font-bold">
            {quote?.price != null ? formatCurrency(quote.price, ccy) : "—"}
          </span>
          {quote && (
            <span className={`text-lg font-medium ${pnlColor(quote.change)}`}>
              {(quote.change ?? 0) >= 0 ? "+" : ""}
              {formatCurrency(quote.change ?? 0, ccy)} ({formatPct(quote.change_pct)})
            </span>
          )}
          {isPriceUp ? <ArrowUp className="w-5 h-5 text-emerald-500" /> : <ArrowDown className="w-5 h-5 text-red-500" />}
        </div>

        {/* Quick stats grid */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Stat label="Day range" value={
            details?.quote.day_low && details?.quote.day_high
              ? `${formatCurrency(details.quote.day_low, ccy)} – ${formatCurrency(details.quote.day_high, ccy)}`
              : "—"
          } />
          <Stat label="52w range" value={
            details?.quote.fifty_two_week_low && details?.quote.fifty_two_week_high
              ? `${formatCurrency(details.quote.fifty_two_week_low, ccy)} – ${formatCurrency(details.quote.fifty_two_week_high, ccy)}`
              : "—"
          } />
          <Stat label="Volume" value={
            details?.quote.volume
              ? formatLargeNumber(details.quote.volume)
              : "—"
          } subValue={
            details?.quote.avg_volume ? `avg ${formatLargeNumber(details.quote.avg_volume)}` : undefined
          } />
          <Stat label="Market cap" value={
            details?.quote.market_cap ? formatLargeNumber(details.quote.market_cap) : "—"
          } />
        </div>
      </div>

      {/* ── Section 2: Chart ────────────────────────────────────────────────── */}
      <div className="rounded-xl border border-border bg-card overflow-hidden">
        <InlineChart ticker={ticker} quote={quote} />
      </div>

      {/* Sections that need details — show skeletons until loaded */}
      {detailsLoading && !details && (
        <div className="rounded-xl border border-border bg-card p-6 text-sm text-muted-foreground">
          Loading fundamentals…
        </div>
      )}

      {/* ── Section 3: Valuation ────────────────────────────────────────────── */}
      {details?.valuation && (
        <Section title="Valuation">
          <Stat label="P/E (trailing)" value={fmtNum(details.valuation.pe_trailing)} />
          <Stat label="P/E (forward)" value={fmtNum(details.valuation.pe_forward)} />
          <Stat label="P/B" value={fmtNum(details.valuation.pb_ratio)} />
          <Stat label="PEG" value={fmtNum(details.valuation.peg_ratio)} />
          <Stat label="EV/EBITDA" value={fmtNum(details.valuation.ev_ebitda)} />
          <Stat label="EPS (TTM)" value={fmtNum(details.valuation.eps_trailing)} />
          <Stat label="EPS (forward)" value={fmtNum(details.valuation.eps_forward)} />
          <Stat label="Dividend yield" value={fmtPct(details.valuation.dividend_yield_pct)} />
        </Section>
      )}

      {/* ── Section 4: Growth & Profitability ───────────────────────────────── */}
      {details?.growth && (
        <Section title="Growth & Profitability">
          <Stat label="Revenue (TTM)" value={details.growth.revenue_ttm ? formatLargeNumber(details.growth.revenue_ttm) : "—"} />
          <Stat label="Revenue growth" value={fmtPct(details.growth.revenue_growth_pct)} valueClass={pnlColor(details.growth.revenue_growth_pct)} />
          <Stat label="Earnings growth" value={fmtPct(details.growth.earnings_growth_pct)} valueClass={pnlColor(details.growth.earnings_growth_pct)} />
          <Stat label="Net margin" value={fmtPct(details.growth.net_margin_pct)} />
          <Stat label="Operating margin" value={fmtPct(details.growth.operating_margin_pct)} />
          <Stat label="ROE" value={fmtPct(details.growth.roe_pct)} />
          <Stat label="Free cash flow" value={details.growth.free_cash_flow ? formatLargeNumber(details.growth.free_cash_flow) : "—"} />
          <Stat label="Debt/Equity" value={fmtNum(details.growth.debt_to_equity)} />
        </Section>
      )}

      {/* ── Section 5: Technicals ───────────────────────────────────────────── */}
      {details?.technicals && (
        <Section title="Technicals">
          <Stat label="RSI 14" value={fmtNum(details.technicals.rsi14)} valueClass={
            details.technicals.rsi14 != null
              ? (details.technicals.rsi14 < 30 ? "text-emerald-500" : details.technicals.rsi14 > 70 ? "text-red-500" : "")
              : ""
          } />
          <Stat label="MACD signal" value={details.technicals.macd_signal ?? "—"} valueClass={
            details.technicals.macd_signal === "bullish" ? "text-emerald-500"
              : details.technicals.macd_signal === "bearish" ? "text-red-500" : ""
          } />
          <Stat label="Above SMA 50" value={
            details.technicals.above_sma50 == null ? "—" : details.technicals.above_sma50 ? "✓ Yes" : "✗ No"
          } valueClass={details.technicals.above_sma50 ? "text-emerald-500" : details.technicals.above_sma50 === false ? "text-red-500" : ""} />
          <Stat label="Above SMA 200" value={
            details.technicals.above_sma200 == null ? "—" : details.technicals.above_sma200 ? "✓ Yes" : "✗ No"
          } valueClass={details.technicals.above_sma200 ? "text-emerald-500" : details.technicals.above_sma200 === false ? "text-red-500" : ""} />
          <Stat label="Beta" value={fmtNum(details.technicals.beta)} />
        </Section>
      )}

      {/* ── Section 6: Options Market (IV) ──────────────────────────────────── */}
      {details?.iv_analytics && (
        <Section title="Options Market">
          <Stat label="ATM IV" value={`${(details.iv_analytics.atm_iv * 100).toFixed(1)}%`} />
          <Stat label="HV (20d)" value={details.iv_analytics.hv_20 != null ? `${(details.iv_analytics.hv_20 * 100).toFixed(1)}%` : "—"} />
          <Stat
            label="IV/HV"
            value={fmtNum(details.iv_analytics.iv_hv_ratio)}
            subValue={
              details.iv_analytics.iv_hv_ratio != null
                ? (details.iv_analytics.iv_hv_ratio > 1.5 ? "expensive vol"
                   : details.iv_analytics.iv_hv_ratio < 0.8 ? "cheap vol"
                   : "normal")
                : undefined
            }
            valueClass={
              details.iv_analytics.iv_hv_ratio != null
                ? (details.iv_analytics.iv_hv_ratio > 1.5 ? "text-red-500"
                   : details.iv_analytics.iv_hv_ratio < 0.8 ? "text-emerald-500" : "")
                : ""
            }
          />
          <Stat label="Implied move" value={fmtPct(details.iv_analytics.implied_move_pct)} subValue={`by ${details.iv_analytics.expiry_used}`} />
          <Stat label="Skew" value={details.iv_analytics.skew != null ? `${(details.iv_analytics.skew * 100).toFixed(1)}%` : "—"} subValue={
            details.iv_analytics.skew != null && details.iv_analytics.skew > 0.08 ? "fear premium" : undefined
          } />
          <Stat label="Days to earnings" value={details.iv_analytics.days_to_earnings != null ? `${details.iv_analytics.days_to_earnings}d` : "—"} />
        </Section>
      )}

      {/* ── Section 7: Live Signals ─────────────────────────────────────────── */}
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">Live Signals</h2>
        </div>
        {tickerAgg && (
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-center gap-3 flex-wrap">
              <span className={`text-sm font-semibold px-2 py-1 rounded ${
                tickerAgg.overall_direction === "bullish" ? "bg-emerald-500/10 text-emerald-500"
                  : tickerAgg.overall_direction === "bearish" ? "bg-red-500/10 text-red-500"
                  : "bg-yellow-500/10 text-yellow-500"
              }`}>
                Overall: {tickerAgg.overall_direction}
              </span>
              <span className="text-xs text-muted-foreground">
                {tickerAgg.total_bullish} bullish · {tickerAgg.total_bearish} bearish · {tickerAgg.category_count} categories
              </span>
            </div>
            <div className="mt-3 space-y-1.5">
              {tickerAgg.categories.map((c) => (
                <div key={c.signal_type} className="flex items-center gap-3 text-sm">
                  <span className="font-medium min-w-[110px]">{c.signal_type.replace(/_/g, " ")}</span>
                  <span className={`text-xs ${
                    c.net_direction === "bullish" ? "text-emerald-500"
                      : c.net_direction === "bearish" ? "text-red-500" : "text-yellow-500"
                  }`}>
                    {c.net_direction} ({"★".repeat(c.net_strength)})
                  </span>
                  <span className="text-xs text-muted-foreground ml-auto">
                    {c.bullish_count}B / {c.bearish_count}S · {c.confidence}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
        {signals.length === 0 && (
          <p className="text-sm text-muted-foreground">No active signals. Click <strong>Scan</strong> above to check now.</p>
        )}
      </div>

      {/* ── Section 8: Analyst Recommendations ──────────────────────────────── */}
      {details?.analyst && (details.analyst.n_analysts ?? 0) > 0 && (
        <Section title="Analyst Recommendations">
          <Stat label="Recommendation" value={details.analyst.recommendation_key ?? "—"} valueClass={
            details.analyst.recommendation_key === "buy" || details.analyst.recommendation_key === "strong_buy"
              ? "text-emerald-500"
              : details.analyst.recommendation_key === "sell" || details.analyst.recommendation_key === "strong_sell"
              ? "text-red-500"
              : ""
          } />
          <Stat label="Number of analysts" value={String(details.analyst.n_analysts ?? "—")} />
          <Stat label="Mean target" value={details.analyst.target_mean != null ? formatCurrency(details.analyst.target_mean, ccy) : "—"} subValue={
            details.analyst.upside_pct != null ? `${details.analyst.upside_pct >= 0 ? "+" : ""}${details.analyst.upside_pct.toFixed(2)}% upside` : undefined
          } />
          <Stat label="High / Low" value={
            details.analyst.target_high && details.analyst.target_low
              ? `${formatCurrency(details.analyst.target_high, ccy)} / ${formatCurrency(details.analyst.target_low, ccy)}`
              : "—"
          } />
        </Section>
      )}

      {/* ── Section 9: News (on-demand) ─────────────────────────────────────── */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">News</h2>
          {!newsExpanded && (
            <button
              onClick={() => setNewsExpanded(true)}
              className="text-xs text-primary hover:underline"
            >
              Load news
            </button>
          )}
        </div>
        {newsExpanded && newsFetching && <p className="text-sm text-muted-foreground">Fetching news…</p>}
        {newsExpanded && !newsFetching && news.length === 0 && (
          <p className="text-sm text-muted-foreground">No recent news found.</p>
        )}
        {news.length > 0 && (
          <div className="space-y-2">
            {news.map((n, i) => (
              <a
                key={n.id ?? i}
                href={n.url ?? "#"}
                target="_blank"
                rel="noopener noreferrer"
                className="block rounded-lg border border-border bg-card p-3 hover:bg-secondary/30 transition-colors"
              >
                <p className="text-sm font-medium">{n.headline}</p>
                <p className="text-xs text-muted-foreground mt-1">
                  {n.source} · {n.published_at ? new Date(n.published_at).toLocaleDateString() : ""}
                  {n.sentiment_score != null && (
                    <span className={`ml-2 ${n.sentiment_score >= 0.2 ? "text-emerald-500" : n.sentiment_score <= -0.2 ? "text-red-500" : "text-muted-foreground"}`}>
                      sentiment {n.sentiment_score.toFixed(2)}
                    </span>
                  )}
                </p>
              </a>
            ))}
          </div>
        )}
      </div>

      {/* ── Section 10: Insider activity (on-demand) ────────────────────────── */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">Insider Activity</h2>
          {!insiderExpanded && (
            <button
              onClick={() => setInsiderExpanded(true)}
              className="text-xs text-primary hover:underline"
            >
              Load insider trades
            </button>
          )}
        </div>
        {insiderExpanded && insiderFetching && <p className="text-sm text-muted-foreground">Fetching SEC filings…</p>}
        {insiderExpanded && !insiderFetching && insider.length === 0 && (
          <p className="text-sm text-muted-foreground">No recent Form 4 filings.</p>
        )}
        {insider.length > 0 && (
          <div className="overflow-x-auto rounded-lg border border-border bg-card">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-secondary/30">
                  <th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Date</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Insider</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Title</th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Action</th>
                  <th className="px-3 py-2 text-right text-xs font-medium text-muted-foreground">Shares</th>
                  <th className="px-3 py-2 text-right text-xs font-medium text-muted-foreground">Value</th>
                </tr>
              </thead>
              <tbody>
                {insider.map((t) => (
                  <tr key={t.id} className="border-b border-border/50 last:border-0">
                    <td className="px-3 py-2 text-muted-foreground">{t.filed_at?.slice(0, 10) ?? "—"}</td>
                    <td className="px-3 py-2">{t.insider_name}</td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">{t.insider_title ?? "—"}</td>
                    <td className={`px-3 py-2 text-xs uppercase ${t.transaction_type === "buy" ? "text-emerald-500" : t.transaction_type === "sell" ? "text-red-500" : ""}`}>
                      {t.transaction_type ?? "—"}
                    </td>
                    <td className="px-3 py-2 text-right">{t.shares?.toLocaleString() ?? "—"}</td>
                    <td className="px-3 py-2 text-right">{t.total_value ? `$${formatLargeNumber(t.total_value)}` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Helpers ────────────────────────────────────────────────────────────── */

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-3">
      <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">{title}</h2>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">{children}</div>
    </div>
  );
}

function Stat({ label, value, subValue, valueClass }: {
  label: string; value: string; subValue?: string; valueClass?: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={`text-sm font-semibold mt-1 truncate ${valueClass ?? ""}`}>{value}</p>
      {subValue && <p className="text-[10px] text-muted-foreground/70 mt-0.5">{subValue}</p>}
    </div>
  );
}

function fmtNum(v: number | null | undefined): string {
  if (v == null) return "—";
  return v.toFixed(v < 10 ? 2 : 1);
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function formatLargeNumber(n: number): string {
  const abs = Math.abs(n);
  if (abs >= 1e12) return (n / 1e12).toFixed(2) + "T";
  if (abs >= 1e9)  return (n / 1e9).toFixed(2) + "B";
  if (abs >= 1e6)  return (n / 1e6).toFixed(2) + "M";
  if (abs >= 1e3)  return (n / 1e3).toFixed(2) + "K";
  return n.toLocaleString();
}
