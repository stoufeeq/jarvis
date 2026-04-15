"use client";

import { useState } from "react";
import { useQuery, useQueries } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";
import { portfolioApi, signalsApi, accountsApi, marketApi } from "@/lib/api";
import { formatCurrency, formatPct, pnlColor, currencyLabel } from "@/lib/utils";
import { useCurrencyDisplay } from "@/hooks/useCurrencyDisplay";
import { CurrencySwitcher } from "@/components/ui/CurrencySwitcher";
import { PrivacyToggle } from "@/components/ui/PrivacyToggle";
import { usePrivacyStore } from "@/store/privacy";
import type { Portfolio, Signal, LiquidityResponse } from "@/types";

const MASK = "••••••";

export default function DashboardPage() {
  const { data: portfolios = [] } = useQuery<Portfolio[]>({
    queryKey: ["portfolios"],
    queryFn: () => portfolioApi.list().then((r) => r.data),
    staleTime: 60_000,
  });

  const { data: signals = [] } = useQuery<Signal[]>({
    queryKey: ["signals", "recent"],
    queryFn: () => signalsApi.list({ limit: 10 }).then((r) => r.data),
    staleTime: 60_000,
  });

  const { data: liquidity } = useQuery<LiquidityResponse>({
    queryKey: ["liquidity"],
    queryFn: () => accountsApi.liquidity().then((r) => r.data),
    staleTime: 60_000,
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

  const portfolioValue = portfolios.reduce((s, p) => s + toUsd(p.total_value ?? 0, p.currency || "USD"), 0);
  const liquidityUsd = liquidity?.total_usd ?? 0;
  const totalValue = portfolioValue + liquidityUsd;
  const totalPnl = portfolios.reduce((s, p) => s + toUsd(p.total_pnl ?? 0, p.currency || "USD"), 0);
  const totalDayChange = portfolios.reduce((s, p) => s + toUsd(p.day_change ?? 0, p.currency || "USD"), 0);
  const prevTotal = portfolioValue - totalDayChange;
  const totalDayChangePct = prevTotal ? (totalDayChange / prevTotal) * 100 : null;

  const mv = (val: string) => (isPrivate ? MASK : val);

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
          label="Liquidity"
          value={mv(formatCurrency(convert(liquidityUsd), displayCurrency))}
          note={isPrivate ? undefined : currencyLabel(displayCurrency)}
          valueClass="text-sky-400"
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
      <p className={`text-lg sm:text-2xl font-bold mt-1 truncate ${valueClass ?? ""}`}>{value}</p>
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
            <span className="text-xs text-muted-foreground">{signal.signal_type.replace("_", " ")}</span>
            {signal.timeframe && (
              <span className="text-xs text-muted-foreground">{signal.timeframe}</span>
            )}
          </div>
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

      {/* Expanded — rationale + indicators */}
      {expanded && (
        <div className="mt-3 pt-3 border-t border-border space-y-1.5">
          {signal.rationale && (
            <p className="text-sm text-muted-foreground">{signal.rationale}</p>
          )}
          {signal.indicators && (
            <p className="text-xs text-muted-foreground/60 font-mono">{signal.indicators}</p>
          )}
        </div>
      )}
    </div>
  );
}
