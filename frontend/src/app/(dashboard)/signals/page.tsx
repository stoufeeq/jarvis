"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { signalsApi, marketApi, portfolioApi } from "@/lib/api";
import { formatCurrency } from "@/lib/utils";
import type { Portfolio, Signal, OptionsFlowSummary, UnusualContract, UWFlowItem, SignalPerformance, PerformanceTimeframe, PerformanceByTimeframe, BacktestResult } from "@/types";
import { useTradingModeStore } from "@/store/tradingMode";
import toast from "react-hot-toast";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer, ReferenceLine } from "recharts";

const SIGNAL_TYPE_LABEL: Record<string, string> = {
  technical:         "Technical",
  insider:           "Insider",
  ai_news:           "AI News",
  options_flow:      "Options Flow",
  fundamental:       "Fundamental",
  earnings_upcoming: "Earnings",
  macro_event:       "Macro Event",
  cross_impact:      "Cross-Impact",
};

const SIGNAL_TYPE_STYLE: Record<string, string> = {
  technical:         "bg-blue-500/10 text-blue-400",
  insider:           "bg-purple-500/10 text-purple-400",
  ai_news:           "bg-sky-500/10 text-sky-400",
  options_flow:      "bg-orange-500/10 text-orange-400",
  fundamental:       "bg-teal-500/10 text-teal-400",
  earnings_upcoming: "bg-yellow-500/10 text-yellow-400",
  macro_event:       "bg-pink-500/10 text-pink-400",
  cross_impact:      "bg-indigo-500/10 text-indigo-400",
};

const DIRECTIONS = ["", "bullish", "bearish", "neutral"] as const;
const TYPES = ["", "technical", "insider", "ai_news", "options_flow", "fundamental", "earnings_upcoming", "macro_event", "cross_impact"] as const;
const TABS = ["signals", "options_flow", "performance", "backtest"] as const;
type Tab = (typeof TABS)[number];

const TAB_LABELS: Record<Tab, string> = {
  signals: "Signals",
  options_flow: "Options Flow",
  performance: "Performance",
  backtest: "Backtest",
};

export default function SignalsPage() {
  const [activeTab, setActiveTab] = useState<Tab>("signals");

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Signals</h1>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-border">
        {TABS.map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              activeTab === tab
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {TAB_LABELS[tab]}
          </button>
        ))}
      </div>

      {activeTab === "signals" && <SignalsTab />}
      {activeTab === "options_flow" && <OptionsFlowTab />}
      {activeTab === "performance" && <PerformanceTab />}
      {activeTab === "backtest" && <BacktestTab />}
    </div>
  );
}

/* ── Signals tab ──────────────────────────────────────────────────────────── */

function SignalsTab() {
  const [ticker, setTicker] = useState("");
  const [direction, setDirection] = useState<string>("");
  const [type, setType] = useState<string>("");
  const [scanTicker, setScanTicker] = useState("");
  const [includeAi, setIncludeAi] = useState(false);

  const { data: signals = [], isLoading, refetch } = useQuery<Signal[]>({
    queryKey: ["signals", ticker, direction, type],
    queryFn: () =>
      signalsApi
        .list({
          ...(ticker && { ticker: ticker.toUpperCase() }),
          ...(direction && { direction }),
          ...(type && { signal_type: type }),
          limit: 50,
        })
        .then((r) => r.data),
  });

  const scanMutation = useMutation({
    mutationFn: (t: string) => signalsApi.scan(t.toUpperCase(), includeAi),
    onSuccess: (res) => {
      const count = res.data.length;
      toast.success(
        count > 0
          ? `${count} signal(s) found for ${scanTicker.toUpperCase()}`
          : `No signals found for ${scanTicker.toUpperCase()}`
      );
      refetch();
    },
    onError: () => toast.error("Scan failed"),
  });

  return (
    <div className="space-y-6">
      {/* On-demand scan */}
      <div className="rounded-xl border border-border bg-card p-4 space-y-3">
        <div className="flex gap-2 items-center flex-wrap">
          <input
            value={scanTicker}
            onChange={(e) => setScanTicker(e.target.value.toUpperCase())}
            placeholder="Ticker e.g. AAPL"
            className="px-3 py-2 rounded-md border border-border bg-input text-sm w-36 focus:outline-none focus:ring-2 focus:ring-ring"
            onKeyDown={(e) =>
              e.key === "Enter" && scanTicker && scanMutation.mutate(scanTicker)
            }
          />
          <button
            onClick={() => scanMutation.mutate(scanTicker)}
            disabled={!scanTicker || scanMutation.isPending}
            className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50"
          >
            {scanMutation.isPending ? "Scanning…" : "Scan Now"}
          </button>
        </div>
        {/* AI toggle */}
        <label className="flex items-center gap-2.5 cursor-pointer w-fit">
          <button
            role="switch"
            aria-checked={includeAi}
            onClick={() => setIncludeAi((v) => !v)}
            className={`relative inline-flex h-5 w-9 shrink-0 rounded-full border-2 border-transparent transition-colors focus:outline-none ${
              includeAi ? "bg-indigo-500" : "bg-muted"
            }`}
          >
            <span
              className={`pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${
                includeAi ? "translate-x-4" : "translate-x-0"
              }`}
            />
          </button>
          <span className="text-sm text-muted-foreground">
            Include AI analysis
            <span className={`ml-1.5 text-xs ${includeAi ? "text-indigo-400" : "text-muted-foreground/50"}`}>
              {includeAi ? "(AI News + Cross-Impact — uses Gemini)" : "(off — no Gemini calls)"}
            </span>
          </span>
        </label>
      </div>

      {/* Filters */}
      <div className="flex gap-3 flex-wrap">
        <input
          value={ticker}
          onChange={(e) => setTicker(e.target.value.toUpperCase())}
          placeholder="Filter by ticker"
          className="px-3 py-2 rounded-md border border-border bg-input text-sm w-36 focus:outline-none focus:ring-2 focus:ring-ring"
        />
        <select
          value={direction}
          onChange={(e) => setDirection(e.target.value)}
          className="px-3 py-2 rounded-md border border-border bg-input text-sm"
        >
          {DIRECTIONS.map((d) => (
            <option key={d} value={d}>
              {d || "All directions"}
            </option>
          ))}
        </select>
        <select
          value={type}
          onChange={(e) => setType(e.target.value)}
          className="px-3 py-2 rounded-md border border-border bg-input text-sm"
        >
          {TYPES.map((t) => (
            <option key={t} value={t}>
              {t || "All types"}
            </option>
          ))}
        </select>
      </div>

      {isLoading && <p className="text-muted-foreground text-sm">Loading…</p>}

      {!isLoading && signals.length === 0 && (
        <div className="rounded-xl border border-dashed border-border p-10 text-center text-muted-foreground text-sm">
          No signals yet. Use "Scan Now" above to analyse a ticker.
        </div>
      )}

      <div className="space-y-3 overflow-x-hidden">
        {signals.map((s) => (
          <SignalCard key={s.id} signal={s} />
        ))}
      </div>
    </div>
  );
}

function SignalCard({ signal }: { signal: Signal }) {
  const tradingMode = useTradingModeStore((s) => s.mode);
  const isPaper = tradingMode === "paper";
  const qc = useQueryClient();

  // Fetch portfolios only when in paper mode (so we know if a paper portfolio
  // exists and can target trades at it)
  const { data: portfolios = [] } = useQuery<Portfolio[]>({
    queryKey: ["portfolios"],
    queryFn: () => portfolioApi.list().then((r) => r.data),
    staleTime: 60_000,
    enabled: isPaper,
  });
  const paperPortfolio = portfolios.find((p) => p.broker === "paper" && p.is_active);

  const [showTradeForm, setShowTradeForm] = useState(false);
  const [tradeQty, setTradeQty] = useState("1");

  const tradeMutation = useMutation({
    mutationFn: (action: "buy" | "sell") =>
      portfolioApi.paperTrade(paperPortfolio!.id, {
        ticker: signal.ticker,
        action,
        quantity: parseFloat(tradeQty) || 1,
      }),
    onSuccess: (_, action) => {
      toast.success(`Paper ${action.toUpperCase()} ${tradeQty} ${signal.ticker} executed`);
      setShowTradeForm(false);
      setTradeQty("1");
      qc.invalidateQueries({ queryKey: ["portfolios"] });
      qc.invalidateQueries({ queryKey: ["positions", paperPortfolio!.id] });
      qc.invalidateQueries({ queryKey: ["trades", paperPortfolio!.id] });
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? "Paper trade failed";
      toast.error(msg);
    },
  });

  const dirColor =
    signal.direction === "bullish"
      ? "text-emerald-500 bg-emerald-500/10"
      : signal.direction === "bearish"
      ? "text-red-500 bg-red-500/10"
      : "text-yellow-500 bg-yellow-500/10";

  const strength = signal.strength ?? 0;
  // Suggest the action that matches the signal's direction
  const suggestedAction: "buy" | "sell" =
    signal.direction === "bearish" ? "sell" : "buy";

  return (
    <div className="rounded-xl border border-border bg-card p-4 space-y-3">
      <div className="flex items-center gap-3 flex-wrap">
        <span className="text-lg font-bold">{signal.ticker}</span>
        <span
          className={`text-xs font-semibold px-2 py-0.5 rounded-full uppercase ${dirColor}`}
        >
          {signal.direction}
        </span>
        <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${SIGNAL_TYPE_STYLE[signal.signal_type] ?? "bg-secondary text-muted-foreground"}`}>
          {SIGNAL_TYPE_LABEL[signal.signal_type] ?? signal.signal_type.replace(/_/g, " ")}
        </span>
        {signal.timeframe && (
          <span className="text-xs text-muted-foreground">{signal.timeframe}</span>
        )}
        <span className="text-xs text-muted-foreground ml-auto flex items-center gap-2">
          {"★".repeat(strength)}
          {"☆".repeat(5 - strength)}
          <span title="Scanned at" className="opacity-60">
            {new Date(signal.created_at).toLocaleDateString()}
          </span>
        </span>
      </div>

      {signal.rationale && (
        <p className="text-sm text-muted-foreground break-words">{signal.rationale}</p>
      )}

      {signal.expires_at && (
        <p className="text-xs text-muted-foreground/50">
          Valid until {new Date(signal.expires_at).toLocaleDateString()}
        </p>
      )}

      {signal.indicators && (
        <div className="flex flex-wrap gap-1.5">
          {signal.indicators.split(/,(?=[A-Za-z_])/).map((ind, i) => {
            const [key, val] = ind.trim().split(/[=:](.+)/);
            return (
              <span
                key={i}
                className="inline-flex items-center gap-1 text-[10px] font-mono bg-secondary/50 text-muted-foreground px-2 py-0.5 rounded"
              >
                <span className="text-muted-foreground/60">{key.trim()}</span>
                {val && <><span className="text-muted-foreground/30">:</span><span>{val.trim()}</span></>}
              </span>
            );
          })}
        </div>
      )}

      {/* Paper Trade button — only visible in Paper mode with an existing paper portfolio */}
      {isPaper && paperPortfolio && (
        <div className="border-t border-border/50 pt-3">
          {!showTradeForm ? (
            <button
              onClick={() => setShowTradeForm(true)}
              className="text-xs font-medium px-3 py-1.5 rounded-md bg-amber-500/10 text-amber-500 hover:bg-amber-500/20 transition-colors"
            >
              Paper Trade ({suggestedAction})
            </button>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted-foreground">Quantity:</span>
              <input
                type="number"
                value={tradeQty}
                onChange={(e) => setTradeQty(e.target.value)}
                min="0"
                step="0.0001"
                className="w-24 px-2 py-1 rounded border border-border bg-input text-sm"
              />
              <button
                onClick={() => tradeMutation.mutate("buy")}
                disabled={tradeMutation.isPending}
                className="px-3 py-1 rounded bg-emerald-500 text-emerald-950 text-xs font-medium disabled:opacity-50"
              >
                Buy
              </button>
              <button
                onClick={() => tradeMutation.mutate("sell")}
                disabled={tradeMutation.isPending}
                className="px-3 py-1 rounded bg-red-500 text-red-50 text-xs font-medium disabled:opacity-50"
              >
                Sell
              </button>
              <button
                onClick={() => { setShowTradeForm(false); setTradeQty("1"); }}
                className="px-3 py-1 rounded text-xs text-muted-foreground hover:text-foreground"
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      )}

      {signal.entry_price && (
        <div className="flex gap-6 text-sm pt-1 border-t border-border/50">
          <div>
            <span className="text-muted-foreground text-xs">Entry</span>
            <p className="font-semibold">{formatCurrency(signal.entry_price)}</p>
          </div>
          <div>
            <span className="text-muted-foreground text-xs">Stop Loss</span>
            <p className="font-semibold text-red-400">
              {formatCurrency(signal.stop_loss)}
            </p>
          </div>
          <div>
            <span className="text-muted-foreground text-xs">Take Profit</span>
            <p className="font-semibold text-emerald-400">
              {formatCurrency(signal.take_profit)}
            </p>
          </div>
          {signal.stop_loss && signal.entry_price && signal.take_profit && (
            <div>
              <span className="text-muted-foreground text-xs">R:R</span>
              <p className="font-semibold">
                1 :{" "}
                {Math.abs(
                  (signal.take_profit - signal.entry_price) /
                    (signal.entry_price - signal.stop_loss)
                ).toFixed(1)}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Options Flow tab ─────────────────────────────────────────────────────── */

function OptionsFlowTab() {
  const [inputTicker, setInputTicker] = useState("");
  const [fetchTicker, setFetchTicker] = useState("");

  const {
    data: flow,
    isLoading,
    isError,
    error,
  } = useQuery<OptionsFlowSummary>({
    queryKey: ["options_flow", fetchTicker],
    queryFn: () =>
      marketApi.optionsFlow(fetchTicker).then((r) => r.data),
    enabled: !!fetchTicker,
    staleTime: 60_000,
  });

  const handleFetch = () => {
    if (inputTicker.trim()) setFetchTicker(inputTicker.trim().toUpperCase());
  };

  return (
    <div className="space-y-6">
      {/* Search bar */}
      <div className="flex gap-2 items-center rounded-xl border border-border bg-card p-4">
        <input
          value={inputTicker}
          onChange={(e) => setInputTicker(e.target.value.toUpperCase())}
          placeholder="Ticker e.g. AAPL"
          className="px-3 py-2 rounded-md border border-border bg-input text-sm w-36 focus:outline-none focus:ring-2 focus:ring-ring"
          onKeyDown={(e) => e.key === "Enter" && handleFetch()}
        />
        <button
          onClick={handleFetch}
          disabled={!inputTicker.trim() || isLoading}
          className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50"
        >
          {isLoading ? "Loading…" : "Fetch Flow"}
        </button>
        <span className="text-xs text-muted-foreground">
          Near-term options flow · ~15 min delayed · Unusual Whales real-time if key configured
        </span>
      </div>

      {isError && (
        <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-400">
          {(error as Error)?.message?.includes("404")
            ? `${fetchTicker} has no listed options or data is unavailable.`
            : "Failed to fetch options data. Try again shortly."}
        </div>
      )}

      {flow && <OptionsFlowDisplay flow={flow} />}

      {!flow && !isLoading && !isError && (
        <div className="rounded-xl border border-dashed border-border p-10 text-center text-muted-foreground text-sm">
          Enter a ticker above to view its options flow summary.
        </div>
      )}
    </div>
  );
}

function OptionsFlowDisplay({ flow }: { flow: OptionsFlowSummary }) {
  const pcRatio = flow.pc_ratio;
  const pcColor =
    pcRatio === null
      ? "text-muted-foreground"
      : pcRatio < 0.7
      ? "text-emerald-400"
      : pcRatio > 1.5
      ? "text-red-400"
      : "text-yellow-400";

  const pcLabel =
    pcRatio === null
      ? "N/A"
      : pcRatio < 0.7
      ? "Bullish"
      : pcRatio > 1.5
      ? "Bearish"
      : "Neutral";

  const totalVol = flow.call_volume + flow.put_volume;
  const callPct = totalVol > 0 ? Math.round((flow.call_volume / totalVol) * 100) : 50;
  const putPct = 100 - callPct;

  const totalPrem = flow.net_call_premium + flow.net_put_premium;
  const callPremPct =
    totalPrem > 0 ? Math.round((flow.net_call_premium / totalPrem) * 100) : 50;
  const putPremPct = 100 - callPremPct;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h2 className="text-xl font-bold">{flow.ticker}</h2>
          <p className="text-xs text-muted-foreground">
            Expiries: {flow.expirations_used.join(", ")} ·{" "}
            {flow.current_price ? `Current: $${flow.current_price}` : ""} ·{" "}
            As of {new Date(flow.as_of).toLocaleTimeString()}
          </p>
        </div>
        {flow.uw_flow && flow.uw_flow.length > 0 && (
          <span className="text-xs bg-indigo-600/20 text-indigo-400 border border-indigo-600/30 px-2 py-1 rounded-full">
            Unusual Whales live data active
          </span>
        )}
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <SummaryCard
          label="Put/Call Ratio"
          value={pcRatio?.toFixed(2) ?? "N/A"}
          sub={pcLabel}
          valueClass={pcColor}
        />
        <SummaryCard
          label="Call Volume"
          value={flow.call_volume.toLocaleString()}
          sub="contracts"
          valueClass="text-emerald-400"
        />
        <SummaryCard
          label="Put Volume"
          value={flow.put_volume.toLocaleString()}
          sub="contracts"
          valueClass="text-red-400"
        />
        <SummaryCard
          label="Net Flow"
          value={
            flow.net_call_premium > flow.net_put_premium
              ? `+$${((flow.net_call_premium - flow.net_put_premium) / 1000).toFixed(0)}k`
              : `-$${((flow.net_put_premium - flow.net_call_premium) / 1000).toFixed(0)}k`
          }
          sub={flow.net_call_premium > flow.net_put_premium ? "Call dominated" : "Put dominated"}
          valueClass={
            flow.net_call_premium > flow.net_put_premium
              ? "text-emerald-400"
              : "text-red-400"
          }
        />
      </div>

      {/* Volume bar */}
      <div className="rounded-xl border border-border bg-card p-4 space-y-3">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          Volume Distribution
        </p>
        <div className="flex rounded-full overflow-hidden h-4">
          <div
            className="bg-emerald-500 transition-all"
            style={{ width: `${callPct}%` }}
            title={`Calls ${callPct}%`}
          />
          <div
            className="bg-red-500 transition-all"
            style={{ width: `${putPct}%` }}
            title={`Puts ${putPct}%`}
          />
        </div>
        <div className="flex justify-between text-xs text-muted-foreground">
          <span className="text-emerald-400">Calls {callPct}%</span>
          <span className="text-red-400">Puts {putPct}%</span>
        </div>
      </div>

      {/* Premium bar */}
      <div className="rounded-xl border border-border bg-card p-4 space-y-3">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          Premium Flow
        </p>
        <div className="flex rounded-full overflow-hidden h-4">
          <div
            className="bg-emerald-500/70 transition-all"
            style={{ width: `${callPremPct}%` }}
            title={`Call premium $${(flow.net_call_premium / 1000).toFixed(0)}k`}
          />
          <div
            className="bg-red-500/70 transition-all"
            style={{ width: `${putPremPct}%` }}
            title={`Put premium $${(flow.net_put_premium / 1000).toFixed(0)}k`}
          />
        </div>
        <div className="flex justify-between text-xs text-muted-foreground">
          <span className="text-emerald-400">
            Call premium ${(flow.net_call_premium / 1000).toFixed(0)}k ({callPremPct}%)
          </span>
          <span className="text-red-400">
            Put premium ${(flow.net_put_premium / 1000).toFixed(0)}k ({putPremPct}%)
          </span>
        </div>
      </div>

      {/* Unusual contracts */}
      <div className="grid md:grid-cols-2 gap-6">
        <ContractTable
          title="Unusual Calls"
          contracts={flow.unusual_calls}
          side="call"
          currentPrice={flow.current_price}
        />
        <ContractTable
          title="Unusual Puts"
          contracts={flow.unusual_puts}
          side="put"
          currentPrice={flow.current_price}
        />
      </div>

      {/* Unusual Whales live flow */}
      {flow.uw_flow && flow.uw_flow.length > 0 && (
        <UWFlowTable items={flow.uw_flow} />
      )}
    </div>
  );
}

function SummaryCard({
  label,
  value,
  sub,
  valueClass = "",
}: {
  label: string;
  value: string;
  sub?: string;
  valueClass?: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <p className="text-xs text-muted-foreground uppercase tracking-wide mb-1">{label}</p>
      <p className={`text-2xl font-bold ${valueClass}`}>{value}</p>
      {sub && <p className="text-xs text-muted-foreground mt-0.5">{sub}</p>}
    </div>
  );
}

function ContractTable({
  title,
  contracts,
  side,
  currentPrice,
}: {
  title: string;
  contracts: UnusualContract[];
  side: "call" | "put";
  currentPrice: number | null;
}) {
  const accent = side === "call" ? "text-emerald-400" : "text-red-400";

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      <div className="px-4 py-3 border-b border-border">
        <h3 className={`text-sm font-semibold ${accent}`}>{title}</h3>
      </div>
      {contracts.length === 0 ? (
        <p className="text-xs text-muted-foreground p-4">No unusual {side} activity detected.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border text-muted-foreground">
                <th className="text-left px-3 py-2">Strike</th>
                <th className="text-left px-3 py-2">Expiry</th>
                <th className="text-right px-3 py-2">Vol</th>
                <th className="text-right px-3 py-2">OI</th>
                <th className="text-right px-3 py-2">Vol/OI</th>
                <th className="text-right px-3 py-2">Premium</th>
                <th className="text-center px-3 py-2">ITM</th>
              </tr>
            </thead>
            <tbody>
              {contracts.map((c, i) => (
                <tr key={i} className="border-b border-border/50 hover:bg-secondary/30">
                  <td className={`px-3 py-2 font-semibold ${accent}`}>
                    ${c.strike}
                    {currentPrice && (
                      <span className="text-muted-foreground font-normal ml-1">
                        ({c.strike > (currentPrice ?? 0) ? "+" : ""}
                        {(((c.strike - (currentPrice ?? 0)) / (currentPrice ?? 1)) * 100).toFixed(1)}
                        %)
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">{c.expiry}</td>
                  <td className="px-3 py-2 text-right">{c.volume.toLocaleString()}</td>
                  <td className="px-3 py-2 text-right text-muted-foreground">
                    {c.open_interest.toLocaleString()}
                  </td>
                  <td className="px-3 py-2 text-right font-semibold">
                    {c.vol_oi_ratio >= 999 ? "∞" : `${c.vol_oi_ratio}×`}
                  </td>
                  <td className="px-3 py-2 text-right">
                    ${(c.premium / 1000).toFixed(0)}k
                  </td>
                  <td className="px-3 py-2 text-center">
                    {c.itm ? (
                      <span className="text-yellow-400">ITM</span>
                    ) : (
                      <span className="text-muted-foreground">OTM</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function UWFlowTable({ items }: { items: UWFlowItem[] }) {
  return (
    <div className="rounded-xl border border-indigo-600/30 bg-card overflow-hidden">
      <div className="px-4 py-3 border-b border-indigo-600/30 flex items-center gap-2">
        <h3 className="text-sm font-semibold text-indigo-400">
          Unusual Whales — Real-Time Flow
        </h3>
        <span className="text-xs text-muted-foreground">
          ({items.length} recent trades)
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border text-muted-foreground">
              <th className="text-left px-3 py-2">Type</th>
              <th className="text-left px-3 py-2">Strike</th>
              <th className="text-left px-3 py-2">Expiry</th>
              <th className="text-right px-3 py-2">Premium</th>
              <th className="text-right px-3 py-2">Vol</th>
              <th className="text-center px-3 py-2">Sweep</th>
              <th className="text-center px-3 py-2">Block</th>
              <th className="text-center px-3 py-2">Sentiment</th>
              <th className="text-left px-3 py-2">Time</th>
            </tr>
          </thead>
          <tbody>
            {items.slice(0, 20).map((item, i) => (
              <tr key={i} className="border-b border-border/50 hover:bg-secondary/30">
                <td
                  className={`px-3 py-2 font-semibold uppercase ${
                    item.type === "call" ? "text-emerald-400" : "text-red-400"
                  }`}
                >
                  {item.type}
                </td>
                <td className="px-3 py-2">${item.strike}</td>
                <td className="px-3 py-2 text-muted-foreground">{item.expiry}</td>
                <td className="px-3 py-2 text-right font-semibold">
                  ${(item.premium / 1000).toFixed(0)}k
                </td>
                <td className="px-3 py-2 text-right">{item.volume.toLocaleString()}</td>
                <td className="px-3 py-2 text-center">
                  {item.is_sweep ? <span className="text-yellow-400">⚡</span> : "—"}
                </td>
                <td className="px-3 py-2 text-center">
                  {item.is_block ? <span className="text-indigo-400">◼</span> : "—"}
                </td>
                <td className="px-3 py-2 text-center">
                  <span
                    className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                      item.sentiment === "bullish"
                        ? "bg-emerald-500/20 text-emerald-400"
                        : item.sentiment === "bearish"
                        ? "bg-red-500/20 text-red-400"
                        : "bg-secondary text-muted-foreground"
                    }`}
                  >
                    {item.sentiment || "—"}
                  </span>
                </td>
                <td className="px-3 py-2 text-muted-foreground">
                  {item.executed_at
                    ? new Date(item.executed_at).toLocaleTimeString()
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ── Performance tab ──────────────────────────────────────────────────────── */

const TIMEFRAMES: PerformanceTimeframe[] = ["1d", "5d", "30d", "90d"];

function PerformanceTab() {
  const { data, isLoading, error, refetch } = useQuery<SignalPerformance>({
    queryKey: ["signals", "performance"],
    queryFn: () => signalsApi.performance().then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  });

  const backfill = useMutation({
    mutationFn: () => signalsApi.backfillOutcomes(),
    onSuccess: () => {
      toast.success("Backfill dispatched — refresh in a few minutes to see results");
      // Refetch after delay to give the worker time to process
      setTimeout(() => refetch(), 30_000);
    },
    onError: () => toast.error("Backfill dispatch failed"),
  });

  if (isLoading) {
    return <p className="text-sm text-muted-foreground">Loading performance data…</p>;
  }
  if (error || !data) {
    return <p className="text-sm text-red-400">Failed to load performance data.</p>;
  }
  if (data.total_outcomes === 0) {
    return (
      <div className="rounded-lg border border-border bg-card p-6 space-y-3">
        <p className="text-sm text-foreground">No tracked signal outcomes yet.</p>
        <p className="text-xs text-muted-foreground">
          New signals from this point onward will be tracked automatically. To
          analyse signals already in the database, backfill them using historical
          yfinance prices — this fetches one year of history per ticker and
          computes entry + 1d/5d/30d/90d snapshots retroactively.
        </p>
        <button
          onClick={() => backfill.mutate()}
          disabled={backfill.isPending}
          className="px-4 py-2 text-sm font-medium rounded-md bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          {backfill.isPending ? "Backfilling…" : "Backfill from existing signals"}
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-border bg-secondary/30 px-4 py-3">
        <p className="text-sm text-foreground">
          Tracking <span className="font-semibold">{data.total_outcomes}</span> signal outcomes.
          Hit rate = % of signals where the price moved in the predicted direction.
          Avg gain % is signed for direction (positive = predicted correctly).
        </p>
      </div>

      <PerfSection title="Overall" data={{ "All": data.overall }} />
      <PerfSection title="By Signal Type" data={data.by_signal_type} keyLabel={SIGNAL_TYPE_LABEL} />
      <PerfSection title="By Direction" data={data.by_direction} />
      <PerfSection title="By Strength" data={data.by_strength} />
    </div>
  );
}

function PerfSection({
  title, data, keyLabel,
}: {
  title: string;
  data: Record<string, PerformanceByTimeframe>;
  keyLabel?: Record<string, string>;
}) {
  const keys = Object.keys(data);
  if (keys.length === 0) return null;

  return (
    <div>
      <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-3">
        {title}
      </h3>
      <div className="overflow-x-auto rounded-lg border border-border bg-card">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-secondary/30">
              <th className="px-4 py-2 text-left text-xs font-medium text-muted-foreground">{title.replace("By ", "")}</th>
              {TIMEFRAMES.map((tf) => (
                <th key={tf} className="px-4 py-2 text-center text-xs font-medium text-muted-foreground" colSpan={3}>
                  {tf}
                </th>
              ))}
            </tr>
            <tr className="border-b border-border bg-secondary/20">
              <th className="px-4 py-1.5"></th>
              {TIMEFRAMES.map((tf) => (
                <Sub3 key={tf} />
              ))}
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => {
              const row = data[k];
              const label = keyLabel?.[k] ?? k;
              return (
                <tr key={k} className="border-b border-border/50 last:border-0">
                  <td className="px-4 py-2 font-medium">{label}</td>
                  {TIMEFRAMES.map((tf) => {
                    const cell = row[tf];
                    return (
                      <PerfCells key={tf} cell={cell} />
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Sub3() {
  return (
    <>
      <th className="px-3 py-1.5 text-right text-[10px] font-normal text-muted-foreground/70">Hit %</th>
      <th className="px-3 py-1.5 text-right text-[10px] font-normal text-muted-foreground/70">Avg %</th>
      <th className="px-3 py-1.5 text-right text-[10px] font-normal text-muted-foreground/70">N</th>
    </>
  );
}

function PerfCells({ cell }: { cell: { hit_rate: number | null; avg_gain_pct: number | null; sample_size: number } | undefined }) {
  if (!cell || cell.sample_size === 0) {
    return (
      <>
        <td className="px-3 py-2 text-right text-muted-foreground/40">—</td>
        <td className="px-3 py-2 text-right text-muted-foreground/40">—</td>
        <td className="px-3 py-2 text-right text-muted-foreground/40">0</td>
      </>
    );
  }
  const hitColor = cell.hit_rate != null && cell.hit_rate >= 50 ? "text-emerald-400" : "text-red-400";
  const gainColor = cell.avg_gain_pct != null && cell.avg_gain_pct >= 0 ? "text-emerald-400" : "text-red-400";
  return (
    <>
      <td className={`px-3 py-2 text-right ${hitColor}`}>{cell.hit_rate?.toFixed(1)}%</td>
      <td className={`px-3 py-2 text-right ${gainColor}`}>{(cell.avg_gain_pct ?? 0).toFixed(2)}%</td>
      <td className="px-3 py-2 text-right text-muted-foreground">{cell.sample_size}</td>
    </>
  );
}

/* ── Backtest tab ─────────────────────────────────────────────────────────── */

function BacktestTab() {
  const [signalType, setSignalType] = useState("");
  const [direction, setDirection] = useState("");
  const [minStrength, setMinStrength] = useState(3);
  const [holdPeriod, setHoldPeriod] = useState("5d");
  const [capitalPerTrade, setCapitalPerTrade] = useState(1000);
  const [tickerFilter, setTickerFilter] = useState("");
  const [result, setResult] = useState<BacktestResult | null>(null);

  const runMutation = useMutation({
    mutationFn: () => signalsApi.backtest({
      signal_type: signalType || null,
      direction: direction || null,
      min_strength: minStrength,
      hold_period: holdPeriod,
      capital_per_trade: capitalPerTrade,
      ticker: tickerFilter.trim() || null,
    }),
    onSuccess: (res) => setResult(res.data),
    onError: () => toast.error("Backtest failed"),
  });

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-border bg-secondary/30 px-4 py-3">
        <p className="text-sm text-foreground">
          Simulate a strategy over your tracked <span className="font-semibold">signal_outcomes</span>:
          {" "}filter by criteria, hold each trade for N days at a fixed capital amount,
          and compare cumulative P&L against a SPY buy-and-hold benchmark.
        </p>
      </div>

      {/* Strategy form */}
      <div className="rounded-lg border border-border bg-card p-4 space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Signal type</label>
            <select
              value={signalType}
              onChange={(e) => setSignalType(e.target.value)}
              className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm"
            >
              <option value="">All</option>
              {TYPES.filter((t) => t).map((t) => (
                <option key={t} value={t}>{SIGNAL_TYPE_LABEL[t] ?? t}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Direction</label>
            <select
              value={direction}
              onChange={(e) => setDirection(e.target.value)}
              className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm"
            >
              <option value="">All</option>
              <option value="bullish">Bullish (long)</option>
              <option value="bearish">Bearish (short)</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Min strength</label>
            <select
              value={minStrength}
              onChange={(e) => setMinStrength(parseInt(e.target.value))}
              className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm"
            >
              {[1, 2, 3, 4, 5].map((s) => <option key={s} value={s}>{s}+ stars</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Hold period</label>
            <select
              value={holdPeriod}
              onChange={(e) => setHoldPeriod(e.target.value)}
              className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm"
            >
              <option value="1d">1 day</option>
              <option value="5d">5 days</option>
              <option value="30d">30 days</option>
              <option value="90d">90 days</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Capital per trade ($)</label>
            <input
              type="number"
              value={capitalPerTrade}
              onChange={(e) => setCapitalPerTrade(parseFloat(e.target.value) || 1000)}
              className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm"
              min="0"
              step="100"
            />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Ticker (optional)</label>
            <input
              type="text"
              value={tickerFilter}
              onChange={(e) => setTickerFilter(e.target.value.toUpperCase())}
              placeholder="e.g. AAPL"
              className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm uppercase"
            />
          </div>
        </div>

        <button
          onClick={() => runMutation.mutate()}
          disabled={runMutation.isPending}
          className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50"
        >
          {runMutation.isPending ? "Running…" : "Run backtest"}
        </button>
      </div>

      {/* Results */}
      {result && <BacktestResultsView result={result} />}
    </div>
  );
}

function BacktestResultsView({ result }: { result: BacktestResult }) {
  const m = result.metrics;
  const bm = result.benchmark;

  if (m.n_trades === 0) {
    return (
      <div className="rounded-lg border border-border bg-card p-6">
        <p className="text-sm text-foreground">
          No trades match the strategy. Try lowering min strength, removing the
          ticker filter, or shortening the hold period.
        </p>
      </div>
    );
  }

  const beatsBenchmark = bm.return_pct != null && m.total_return_pct > bm.return_pct;

  return (
    <div className="space-y-6">
      {/* Headline metrics */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <MetricCard
          label="Total return"
          value={`${m.total_return_pct >= 0 ? "+" : ""}${m.total_return_pct.toFixed(2)}%`}
          subValue={`$${m.total_pnl.toLocaleString()} on ${m.n_trades} trades`}
          color={m.total_return_pct >= 0 ? "emerald" : "red"}
        />
        <MetricCard
          label="vs SPY benchmark"
          value={bm.return_pct != null ? `${bm.return_pct >= 0 ? "+" : ""}${bm.return_pct.toFixed(2)}%` : "n/a"}
          subValue={beatsBenchmark ? "Strategy beats SPY ✓" : (bm.return_pct != null ? "SPY beats strategy" : "")}
          color={beatsBenchmark ? "emerald" : "muted"}
        />
        <MetricCard
          label="Hit rate"
          value={`${m.hit_rate_pct.toFixed(1)}%`}
          subValue={`${m.wins} wins / ${m.losses} losses`}
          color={m.hit_rate_pct >= 50 ? "emerald" : "red"}
        />
        <MetricCard
          label="Max drawdown"
          value={`${m.max_drawdown_pct.toFixed(2)}%`}
          subValue={`$${m.max_drawdown.toLocaleString()}`}
          color="red"
        />
      </div>

      <p className="text-xs text-muted-foreground">
        Period: {m.first_date} → {m.last_exit_date}. Avg trade P&L: ${m.avg_trade_pnl.toLocaleString()}.
      </p>

      {/* Equity curve */}
      <div className="rounded-lg border border-border bg-card p-4">
        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">
          Cumulative P&L over time
        </p>
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={result.equity_curve}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
              <XAxis dataKey="date" tick={{ fontSize: 11, fill: "#64748b" }} />
              <YAxis tick={{ fontSize: 11, fill: "#64748b" }} />
              <ReferenceLine y={0} stroke="#475569" strokeDasharray="4 2" />
              <RechartsTooltip
                contentStyle={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 6 }}
                labelStyle={{ color: "#f1f5f9" }}
                formatter={(value: number) => [`$${value.toFixed(2)}`, "Cumulative P&L"]}
              />
              <Line
                type="monotone"
                dataKey="cumulative_pnl"
                stroke={m.total_pnl >= 0 ? "#10b981" : "#ef4444"}
                strokeWidth={2}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Recent trades */}
      <div>
        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">
          Last 10 trades in this strategy
        </p>
        <div className="overflow-x-auto rounded-lg border border-border bg-card">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-secondary/30">
                <th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Date</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Ticker</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Type</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Dir</th>
                <th className="px-3 py-2 text-right text-xs font-medium text-muted-foreground">Trade P&L</th>
                <th className="px-3 py-2 text-right text-xs font-medium text-muted-foreground">Cum P&L</th>
              </tr>
            </thead>
            <tbody>
              {result.equity_curve.slice(-10).reverse().map((p, i) => (
                <tr key={i} className="border-b border-border/50 last:border-0">
                  <td className="px-3 py-2 text-muted-foreground">{p.date}</td>
                  <td className="px-3 py-2 font-medium">{p.ticker}</td>
                  <td className="px-3 py-2 text-muted-foreground text-xs">{SIGNAL_TYPE_LABEL[p.signal_type] ?? p.signal_type}</td>
                  <td className="px-3 py-2 text-xs">{p.direction}</td>
                  <td className={`px-3 py-2 text-right font-medium ${p.trade_pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                    {p.trade_pnl >= 0 ? "+" : ""}${p.trade_pnl.toFixed(2)}
                  </td>
                  <td className={`px-3 py-2 text-right ${p.cumulative_pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                    ${p.cumulative_pnl.toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function MetricCard({
  label, value, subValue, color,
}: {
  label: string;
  value: string;
  subValue?: string;
  color: "emerald" | "red" | "muted";
}) {
  const colorClass = color === "emerald" ? "text-emerald-400" : color === "red" ? "text-red-400" : "text-foreground";
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={`text-xl font-bold mt-1 ${colorClass}`}>{value}</p>
      {subValue && <p className="text-xs text-muted-foreground mt-0.5">{subValue}</p>}
    </div>
  );
}
