"use client";

/**
 * Dividend income tab.
 *
 * Amounts are derived server-side from shares-held-at-ex-date walked from
 * the trade ledger, so they stay correct after back-dated IBKR imports or
 * trade edits. All figures arrive already FX-converted to the portfolio's
 * base currency.
 *
 * Ex-date vs pay-date is surfaced deliberately: entitlement is set on the
 * ex-date, but the cash lands on the pay date, and for most historical
 * events yfinance only gives us the ex-date (hence the em-dash).
 */

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import toast from "react-hot-toast";
import { portfolioApi, type DividendIncome, type DividendEvent } from "@/lib/api";
import { formatCurrency } from "@/lib/utils";
import { TickerLink } from "@/components/ui/TickerLink";

interface Props {
  portfolioId: number;
  /** Portfolio base currency, for formatting. */
  currency?: string;
}

function fmtDate(iso: string): string {
  return new Date(iso + "T00:00:00Z").toLocaleDateString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function DividendsTab({ portfolioId, currency = "USD" }: Props) {
  const qc = useQueryClient();
  const [yearFilter, setYearFilter] = useState<string>("all");

  const { data, isLoading, error } = useQuery<DividendIncome>({
    queryKey: ["dividends", portfolioId],
    queryFn: () => portfolioApi.dividends(portfolioId).then((r) => r.data),
    // Dividend events change on a quarterly cadence; the nightly Celery
    // sync is the real refresh. No need to poll.
    staleTime: 30 * 60 * 1000,
  });

  const sync = useMutation({
    mutationFn: () => portfolioApi.syncDividends(portfolioId),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["dividends", portfolioId] });
      const n = (res.data as { rows_written?: number })?.rows_written ?? 0;
      toast.success(n > 0 ? `Synced — ${n} new event(s)` : "Synced — already up to date");
    },
    onError: () => toast.error("Dividend sync failed"),
  });

  if (isLoading) {
    return (
      <div className="p-8 text-sm text-muted-foreground text-center">
        Loading dividend history…
      </div>
    );
  }
  if (error || !data) {
    return <div className="p-6 text-sm text-red-400">Failed to load dividends.</div>;
  }

  const ccy = data.base_currency || currency;
  const years = Array.from(
    new Set(data.received.map((r) => r.ex_date.slice(0, 4)))
  ).sort((a, b) => b.localeCompare(a));
  const rows =
    yearFilter === "all"
      ? data.received
      : data.received.filter((r) => r.ex_date.startsWith(yearFilter));

  const hasAnything = data.received.length > 0 || data.upcoming.length > 0;

  return (
    <div className="space-y-6">
      {/* ── Summary ─────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat
          label="YTD received"
          value={formatCurrency(data.ytd, ccy)}
          valueClass="text-emerald-500"
        />
        <Stat label="Trailing 12mo" value={formatCurrency(data.trailing_12m, ccy)} />
        <Stat
          label="Forward est. (12mo)"
          value={formatCurrency(data.forward_annual_estimate, ccy)}
          hint={
            data.forward_yield_on_cost_pct != null
              ? `${data.forward_yield_on_cost_pct.toFixed(2)}% yield on cost`
              : "from current holdings × published rates"
          }
        />
        <Stat label="All time" value={formatCurrency(data.total_all_time, ccy)} />
      </div>

      {!hasAnything && (
        <div className="rounded-lg border border-border/50 bg-card p-6 text-sm text-muted-foreground">
          No dividend events recorded yet for this portfolio&apos;s tickers.
          Hit <strong>Sync</strong> to pull history from the market data
          provider — it also runs automatically every night.
          <div className="mt-3">
            <SyncButton pending={sync.isPending} onClick={() => sync.mutate()} />
          </div>
        </div>
      )}

      {/* ── Upcoming ────────────────────────────────────────────── */}
      {data.upcoming.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide mb-2">
            Upcoming
          </h3>
          <div className="rounded-xl border border-border overflow-hidden overflow-x-auto">
            <table className="w-full text-sm min-w-[560px]">
              <thead>
                <tr className="border-b border-border bg-secondary/50 text-muted-foreground">
                  <th className="px-4 py-2 text-left font-medium">Ticker</th>
                  <th className="px-4 py-2 text-left font-medium">Ex-date</th>
                  <th className="px-4 py-2 text-left font-medium">Pay date</th>
                  <th className="px-4 py-2 text-right font-medium">Per share</th>
                  <th className="px-4 py-2 text-right font-medium">Shares</th>
                  <th className="px-4 py-2 text-right font-medium">Amount</th>
                </tr>
              </thead>
              <tbody>
                {data.upcoming.map((d) => (
                  <Row key={`${d.ticker}-${d.ex_date}`} d={d} ccy={ccy} upcoming />
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[11px] text-muted-foreground">
            Sized by your <strong>current</strong> holdings — entitlement is
            only fixed once the ex-date passes, so selling before then
            forfeits the payment.
          </p>
        </section>
      )}

      {/* ── History ─────────────────────────────────────────────── */}
      {data.received.length > 0 && (
        <section>
          <div className="flex items-center justify-between mb-2 gap-3 flex-wrap">
            <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
              Received
            </h3>
            <div className="flex items-center gap-2">
              <select
                value={yearFilter}
                onChange={(e) => setYearFilter(e.target.value)}
                className="px-2 py-1 rounded-md border border-border bg-input text-xs focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <option value="all">All years</option>
                {years.map((y) => (
                  <option key={y} value={y}>{y}</option>
                ))}
              </select>
              <SyncButton pending={sync.isPending} onClick={() => sync.mutate()} />
            </div>
          </div>

          <div className="rounded-xl border border-border overflow-hidden overflow-x-auto">
            <table className="w-full text-sm min-w-[560px]">
              <thead>
                <tr className="border-b border-border bg-secondary/50 text-muted-foreground">
                  <th className="px-4 py-2 text-left font-medium">Ticker</th>
                  <th className="px-4 py-2 text-left font-medium">Ex-date</th>
                  <th className="px-4 py-2 text-left font-medium">Pay date</th>
                  <th className="px-4 py-2 text-right font-medium">Per share</th>
                  <th className="px-4 py-2 text-right font-medium">Shares</th>
                  <th className="px-4 py-2 text-right font-medium">Amount</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((d) => (
                  <Row key={`${d.ticker}-${d.ex_date}`} d={d} ccy={ccy} />
                ))}
              </tbody>
            </table>
          </div>

          {/* By-ticker breakdown */}
          {data.by_ticker.length > 1 && (
            <div className="mt-4">
              <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">
                By ticker (all time)
              </h4>
              <div className="rounded-lg border border-border bg-card p-3 space-y-1.5">
                {data.by_ticker.map((b) => {
                  const max = data.by_ticker[0].amount || 1;
                  const width = (b.amount / max) * 90;
                  return (
                    <div key={b.ticker} className="flex items-center gap-3 text-xs">
                      <span className="w-14 font-semibold tabular-nums">{b.ticker}</span>
                      <div className="flex-1 relative h-4 rounded bg-secondary/40 overflow-hidden">
                        <div
                          className="absolute inset-y-0 left-0 bg-emerald-500/40"
                          style={{ width: `${width}%` }}
                        />
                      </div>
                      <span className="w-24 text-right tabular-nums font-semibold">
                        {formatCurrency(b.amount, ccy)}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          <p className="mt-3 text-[11px] text-muted-foreground">
            Amounts use the shares you held at each <strong>ex-date</strong>,
            reconstructed from your trade ledger — so editing or importing
            back-dated trades updates these figures automatically. A pay
            date of &mdash; means the provider only published the ex-date.
          </p>
        </section>
      )}
    </div>
  );
}

function Row({ d, ccy, upcoming }: { d: DividendEvent; ccy: string; upcoming?: boolean }) {
  return (
    <tr className={`border-b border-border/50 ${upcoming ? "bg-emerald-500/[0.03]" : ""}`}>
      <td className="px-4 py-2 font-semibold">
        <TickerLink ticker={d.ticker} />
      </td>
      <td className="px-4 py-2 text-muted-foreground">{fmtDate(d.ex_date)}</td>
      <td className="px-4 py-2 text-muted-foreground">
        {d.pay_date ? fmtDate(d.pay_date) : "—"}
      </td>
      <td className="px-4 py-2 text-right tabular-nums">
        {formatCurrency(d.amount_per_share, d.currency)}
      </td>
      <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">
        {d.shares.toLocaleString(undefined, { maximumFractionDigits: 4 })}
      </td>
      <td className="px-4 py-2 text-right tabular-nums font-semibold text-emerald-500">
        {formatCurrency(d.amount, ccy)}
      </td>
    </tr>
  );
}

function SyncButton({ pending, onClick }: { pending: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      disabled={pending}
      className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-secondary text-xs font-medium hover:bg-secondary/80 disabled:opacity-50 transition-colors"
      title="Pull fresh dividend history from the market data provider"
    >
      <RefreshCw className={`w-3 h-3 ${pending ? "animate-spin" : ""}`} />
      {pending ? "Syncing…" : "Sync"}
    </button>
  );
}

function Stat({
  label, value, valueClass, hint,
}: { label: string; value: string; valueClass?: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-border/50 bg-card p-3">
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className={`text-lg font-semibold tabular-nums mt-0.5 ${valueClass ?? ""}`}>{value}</p>
      {hint && (
        <p className="text-[10px] text-muted-foreground/70 mt-1 leading-tight">{hint}</p>
      )}
    </div>
  );
}
