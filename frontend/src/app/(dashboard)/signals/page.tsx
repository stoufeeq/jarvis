"use client";

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { signalsApi, marketApi } from "@/lib/api";
import { formatCurrency } from "@/lib/utils";
import type { Signal, OptionsFlowSummary, UnusualContract, UWFlowItem } from "@/types";
import toast from "react-hot-toast";

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
const TABS = ["signals", "options_flow"] as const;
type Tab = (typeof TABS)[number];

export default function SignalsPage() {
  const [activeTab, setActiveTab] = useState<Tab>("signals");

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Signals</h1>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-border">
        {(["signals", "options_flow"] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              activeTab === tab
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {tab === "signals" ? "Signals" : "Options Flow"}
          </button>
        ))}
      </div>

      {activeTab === "signals" && <SignalsTab />}
      {activeTab === "options_flow" && <OptionsFlowTab />}
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
  const dirColor =
    signal.direction === "bullish"
      ? "text-emerald-500 bg-emerald-500/10"
      : signal.direction === "bearish"
      ? "text-red-500 bg-red-500/10"
      : "text-yellow-500 bg-yellow-500/10";

  const strength = signal.strength ?? 0;

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
          {signal.indicators.split(",").map((ind, i) => {
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
