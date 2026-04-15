"use client";

import { useQuery } from "@tanstack/react-query";
import { portfolioApi, signalsApi, accountsApi } from "@/lib/api";
import { formatCurrency, formatPct, pnlColor } from "@/lib/utils";
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
  });

  const { data: signals = [] } = useQuery<Signal[]>({
    queryKey: ["signals", "recent"],
    queryFn: () => signalsApi.list({ limit: 10 }).then((r) => r.data),
  });

  const { data: liquidity } = useQuery<LiquidityResponse>({
    queryKey: ["liquidity"],
    queryFn: () => accountsApi.liquidity().then((r) => r.data),
  });

  const dashboardBase = "USD";
  const { displayCurrency, setDisplayCurrency, rate, convert, base: baseCurrency } =
    useCurrencyDisplay(dashboardBase);

  const isPrivate = usePrivacyStore((s) => s.isPrivate);

  const portfolioValue = portfolios.reduce((s, p) => s + (p.total_value ?? 0), 0);
  const liquidityUsd = liquidity?.total_usd ?? 0;
  const totalValue = portfolioValue + liquidityUsd;
  const totalPnl = portfolios.reduce((s, p) => s + (p.total_pnl ?? 0), 0);
  const totalDayChange = portfolios.reduce((s, p) => s + (p.day_change ?? 0), 0);
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
          note={isPrivate ? undefined : displayCurrency}
        />
        <StatCard
          label="Liquidity"
          value={mv(formatCurrency(convert(liquidityUsd), displayCurrency))}
          note={isPrivate ? undefined : displayCurrency}
          valueClass="text-sky-400"
        />
        <StatCard
          label="Unrealized P&L"
          value={mv(formatCurrency(convert(totalPnl), displayCurrency))}
          valueClass={isPrivate ? undefined : pnlColor(totalPnl)}
          note={isPrivate ? undefined : displayCurrency}
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
  const dirColor =
    signal.direction === "bullish"
      ? "text-emerald-500"
      : signal.direction === "bearish"
      ? "text-red-500"
      : "text-yellow-500";

  return (
    <div className="flex items-start gap-3 rounded-lg border border-border bg-card p-3">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-semibold">{signal.ticker}</span>
          <span className={`text-xs font-medium uppercase ${dirColor}`}>
            {signal.direction}
          </span>
          <span className="text-xs text-muted-foreground">
            {"★".repeat(signal.strength)}{"☆".repeat(5 - signal.strength)}
          </span>
          <span className="text-xs text-muted-foreground">{signal.signal_type}</span>
        </div>
        <p className="text-sm text-muted-foreground mt-1 truncate">{signal.rationale}</p>
      </div>
      {signal.entry_price && (
        <div className="text-right shrink-0 text-xs space-y-0.5">
          <div>Entry: {formatCurrency(signal.entry_price)}</div>
          <div className="text-red-400">SL: {formatCurrency(signal.stop_loss)}</div>
          <div className="text-emerald-400">TP: {formatCurrency(signal.take_profit)}</div>
        </div>
      )}
    </div>
  );
}
