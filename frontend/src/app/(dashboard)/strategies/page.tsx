"use client";

import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { strategiesApi, portfolioApi } from "@/lib/api";
import { TickerLink } from "@/components/ui/TickerLink";
import { formatCurrency, formatPct, pnlColor } from "@/lib/utils";
import type { Strategy, StrategyStats, StrategyTradeDetail, Portfolio } from "@/types";
import toast from "react-hot-toast";
import { Plus, Trash2, Pause, Play, AlertTriangle, X, Pencil } from "lucide-react";

const SIGNAL_TYPES = ["technical", "insider", "ai_news", "options_flow", "fundamental", "earnings_upcoming", "macro_event", "cross_impact"];

// Signal types we surface in the per-type strength override UI. The
// backtest-driven recommendations as of 2026-06-04 are shown as hints
// in the form so users know what each provider's "good" cutoff is.
const OVERRIDE_TYPES: Array<{ key: string; label: string; recommended: number }> = [
  { key: "fundamental",       label: "Fundamental",        recommended: 4 },
  { key: "technical",         label: "Technical",          recommended: 5 },
  { key: "options_flow",      label: "Options flow",       recommended: 3 },
  { key: "insider",           label: "Insider",            recommended: 4 },
  { key: "earnings_upcoming", label: "Earnings upcoming",  recommended: 3 },
];

type StrengthOverrideMap = { [type: string]: number | null };

const EMPTY_OVERRIDES: StrengthOverrideMap = Object.fromEntries(
  OVERRIDE_TYPES.map((t) => [t.key, null])
);

const DEFAULT_CREATE = {
  name: "",
  description: "",
  signal_type: null as string | null,
  direction: "bullish" as "bullish" | "bearish" | null,
  min_strength: 4,
  signal_type_strength_overrides: { ...EMPTY_OVERRIDES } as StrengthOverrideMap,
  tickers: "",
  excluded_tickers: "",
  allocation_mode: "fixed" as "fixed" | "percent",
  allocation_value: 2000,
  max_position_pct: 10,
  min_cash_reserve: 5000,
  min_hold_days: 1,
  base_hold_days: 5,
  // Lowered default 30 → 10 based on paper-trade analysis (long holds
  // consistently amplified losses). Existing strategies keep their
  // configured value; edit in the form below to lower.
  max_hold_days: 10,
  exit_on_opposite_signal: true,
  extend_on_continuing_signal: true,
  // Signed % (negative = long stop-loss); null = disabled.
  stop_loss_pct: -8 as number | null,
};

export default function StrategiesPage() {
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<Strategy | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const { data: strategies = [], isLoading } = useQuery<Strategy[]>({
    queryKey: ["strategies"],
    queryFn: () => strategiesApi.list().then((r) => r.data),
    staleTime: 30_000,
  });

  const { data: portfolios = [] } = useQuery<Portfolio[]>({
    queryKey: ["portfolios"],
    queryFn: () => portfolioApi.list().then((r) => r.data),
  });
  // All active paper portfolios — one strategy per portfolio is the
  // recommended pattern for parallel A/B testing since each has its
  // own cash pool and Position table.
  const paperPortfolios = portfolios.filter((p) => p.broker === "paper" && p.is_active);
  const hasPaper = paperPortfolios.length > 0;
  // Quick lookup for the card summary.
  const portfolioById = new Map(portfolios.map((p) => [p.id, p]));

  return (
    <div className="space-y-6 max-w-5xl">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold">Strategies</h1>
          <p className="text-xs text-muted-foreground mt-1">
            Auto-execute paper trades when signals match your rules. Paper portfolio only — your real positions are never touched.
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          disabled={!hasPaper}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50"
          title={!hasPaper ? "Create a paper portfolio first (toggle Paper mode in header)" : "Create strategy"}
        >
          <Plus className="w-3.5 h-3.5" /> New Strategy
        </button>
      </div>

      {!hasPaper && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4 text-sm">
          <p className="text-amber-500 font-medium">No paper portfolio yet</p>
          <p className="text-muted-foreground mt-1">
            Switch to <strong>Paper</strong> mode in the header and create a paper portfolio to use strategies.
          </p>
        </div>
      )}

      {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}

      {!isLoading && strategies.length === 0 && hasPaper && (
        <div className="rounded-xl border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
          No strategies yet. Click <strong>New Strategy</strong> to create one.
        </div>
      )}

      <div className="space-y-3">
        {strategies.map((s) => (
          <StrategyCard
            key={s.id}
            strategy={s}
            portfolioName={portfolioById.get(s.portfolio_id)?.name ?? "?"}
            expanded={expandedId === s.id}
            onToggleExpand={() => setExpandedId((id) => (id === s.id ? null : s.id))}
            onEdit={() => setEditing(s)}
            onChange={() => qc.invalidateQueries({ queryKey: ["strategies"] })}
          />
        ))}
      </div>

      {showCreate && hasPaper && (
        <CreateStrategyModal
          paperPortfolios={paperPortfolios}
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            qc.invalidateQueries({ queryKey: ["strategies"] });
            setShowCreate(false);
            toast.success("Strategy created");
          }}
        />
      )}

      {editing && hasPaper && (
        <CreateStrategyModal
          paperPortfolios={paperPortfolios}
          existing={editing}
          onClose={() => setEditing(null)}
          onCreated={() => {
            qc.invalidateQueries({ queryKey: ["strategies"] });
            qc.invalidateQueries({ queryKey: ["strategy-stats", editing.id] });
            setEditing(null);
            toast.success("Strategy updated");
          }}
        />
      )}
    </div>
  );
}

/* ── Strategy card ─────────────────────────────────────────────────── */

function StrategyCard({
  strategy: s,
  portfolioName,
  expanded,
  onToggleExpand,
  onEdit,
  onChange,
}: {
  strategy: Strategy;
  portfolioName: string;
  expanded: boolean;
  onToggleExpand: () => void;
  onEdit: () => void;
  onChange: () => void;
}) {
  const qc = useQueryClient();

  const { data: stats } = useQuery<StrategyStats>({
    queryKey: ["strategy-stats", s.id],
    queryFn: () => strategiesApi.stats(s.id).then((r) => r.data),
    staleTime: 60_000,
  });

  const togglePause = useMutation({
    mutationFn: () => strategiesApi.update(s.id, { is_active: !s.is_active }),
    onSuccess: () => {
      onChange();
      toast.success(s.is_active ? "Strategy paused" : "Strategy resumed");
    },
  });

  const remove = useMutation({
    mutationFn: () => strategiesApi.delete(s.id),
    onSuccess: () => {
      onChange();
      toast.success("Strategy deleted");
    },
  });

  const panicClose = useMutation({
    mutationFn: () => strategiesApi.panicClose(s.id),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["strategy-stats", s.id] });
      qc.invalidateQueries({ queryKey: ["strategy-trades", s.id] });
      toast.success(`Closed ${res.data.closed} position(s)`);
    },
    onError: () => toast.error("Panic close failed"),
  });

  const filterSummary = useMemo(() => {
    const parts: string[] = [];
    if (s.signal_type) parts.push(s.signal_type.replace(/_/g, " "));
    if (s.direction) parts.push(s.direction);
    parts.push(`${s.min_strength}★+`);
    if (s.tickers) parts.push(`[${s.tickers.split(",").slice(0, 3).join(",")}…]`);
    return parts.join(" · ");
  }, [s]);

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-3">
        <button onClick={onToggleExpand} className="flex-1 text-left">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold">{s.name}</span>
            {s.is_active ? (
              <span className="text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-500">
                Active
              </span>
            ) : (
              <span className="text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full bg-muted-foreground/10 text-muted-foreground">
                Paused
              </span>
            )}
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">
            <span className="text-foreground/70">→ {portfolioName}</span>
            {" · "}
            {filterSummary}
          </p>
        </button>

        {stats && (
          <div className="hidden sm:flex items-center gap-4 text-xs">
            <Stat label="Open" value={String(stats.open_count)} />
            <Stat label="Closed" value={String(stats.closed_count)} />
            <Stat
              label="P&L"
              value={formatCurrency(stats.total_pnl, "USD")}
              valueClass={pnlColor(stats.total_pnl)}
            />
            {stats.win_rate_pct != null && (
              <Stat label="Win %" value={`${stats.win_rate_pct.toFixed(0)}%`} />
            )}
          </div>
        )}

        <div className="flex items-center gap-1">
          <button
            onClick={onEdit}
            className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
            title="Edit strategy"
          >
            <Pencil className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => togglePause.mutate()}
            disabled={togglePause.isPending}
            className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
            title={s.is_active ? "Pause strategy" : "Resume strategy"}
          >
            {s.is_active ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
          </button>
          <button
            onClick={() => {
              if (confirm(`Close ALL ${stats?.open_count ?? "?"} open positions immediately?`)) {
                panicClose.mutate();
              }
            }}
            disabled={panicClose.isPending || (stats?.open_count ?? 0) === 0}
            className="p-1.5 rounded-md text-muted-foreground hover:text-red-500 hover:bg-secondary transition-colors disabled:opacity-30"
            title="Panic close all open positions"
          >
            <AlertTriangle className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => {
              if (confirm(`Delete strategy "${s.name}"? Closed trade history is preserved.`)) {
                remove.mutate();
              }
            }}
            disabled={remove.isPending}
            className="p-1.5 rounded-md text-muted-foreground hover:text-red-500 hover:bg-secondary transition-colors"
            title="Delete strategy"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {expanded && <StrategyDetail strategy={s} />}
    </div>
  );
}

function Stat({ label, value, valueClass }: { label: string; value: string; valueClass?: string }) {
  return (
    <div>
      <p className="text-[10px] uppercase text-muted-foreground/60">{label}</p>
      <p className={`text-sm font-semibold ${valueClass ?? ""}`}>{value}</p>
    </div>
  );
}

/* ── Strategy detail (trade history + rules) ───────────────────────── */

function StrategyDetail({ strategy: s }: { strategy: Strategy }) {
  const { data: trades = [] } = useQuery<StrategyTradeDetail[]>({
    queryKey: ["strategy-trades", s.id],
    queryFn: () => strategiesApi.trades(s.id).then((r) => r.data),
    staleTime: 60_000,
  });

  return (
    <div className="border-t border-border bg-secondary/10 p-4 space-y-4">
      {/* Rules summary */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
        <DetailField label="Allocation" value={
          s.allocation_mode === "fixed"
            ? formatCurrency(s.allocation_value, "USD") + " per trade"
            : `${s.allocation_value}% of cash`
        } />
        <DetailField label="Max per ticker" value={`${s.max_position_pct}%`} />
        <DetailField label="Min cash reserve" value={formatCurrency(s.min_cash_reserve, "USD")} />
        <DetailField label="Hold (min/base/max)" value={`${s.min_hold_days}d / ${s.base_hold_days}d / ${s.max_hold_days}d`} />
        <DetailField
          label="Stop-loss"
          value={s.stop_loss_pct != null ? `${s.stop_loss_pct}%` : "off"}
        />
        <DetailField label="Exit on opposite signal" value={s.exit_on_opposite_signal ? "Yes" : "No"} />
        <DetailField label="Extend on same signal" value={s.extend_on_continuing_signal ? "Yes" : "No"} />
        {s.excluded_tickers && (
          <DetailField label="Excluded tickers" value={s.excluded_tickers} />
        )}
      </div>

      {/* Trades */}
      <div>
        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
          Trade history ({trades.length})
        </p>
        {trades.length === 0 ? (
          <p className="text-xs text-muted-foreground italic">
            No trades yet. The strategy will fire when matching signals scan.
          </p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border bg-card">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border bg-secondary/30">
                  <th className="px-3 py-2 text-left font-medium text-muted-foreground">Entry</th>
                  <th className="px-3 py-2 text-left font-medium text-muted-foreground">Ticker</th>
                  <th className="px-3 py-2 text-right font-medium text-muted-foreground">Qty</th>
                  <th className="px-3 py-2 text-right font-medium text-muted-foreground">Entry $</th>
                  <th className="px-3 py-2 text-right font-medium text-muted-foreground">Exit $</th>
                  <th className="px-3 py-2 text-right font-medium text-muted-foreground">P&L</th>
                  <th className="px-3 py-2 text-left font-medium text-muted-foreground">Status</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t) => {
                  const pnl = t.exit_price != null
                    ? (Number(t.exit_price) - Number(t.entry_price)) * Number(t.quantity) * (t.direction === "bearish" ? -1 : 1)
                    : null;
                  return (
                    <tr key={t.id} className="border-b border-border/50 last:border-0">
                      <td className="px-3 py-2 text-muted-foreground">{t.entry_at.slice(0, 10)}</td>
                      <td className="px-3 py-2"><TickerLink ticker={t.ticker} className="font-medium" /></td>
                      <td className="px-3 py-2 text-right">{Number(t.quantity).toFixed(4)}</td>
                      <td className="px-3 py-2 text-right">${Number(t.entry_price).toFixed(2)}</td>
                      <td className="px-3 py-2 text-right">{t.exit_price != null ? `$${Number(t.exit_price).toFixed(2)}` : "—"}</td>
                      <td className={`px-3 py-2 text-right ${pnl != null ? pnlColor(pnl) : "text-muted-foreground"}`}>
                        {pnl != null ? formatCurrency(pnl, "USD") : "—"}
                      </td>
                      <td className="px-3 py-2">
                        <span className={`text-[10px] uppercase px-1.5 py-0.5 rounded ${
                          t.status === "open" ? "bg-amber-500/10 text-amber-500" : "bg-secondary/50 text-muted-foreground"
                        }`}>
                          {t.status}{t.exit_reason ? ` · ${t.exit_reason.replace(/_/g, " ")}` : ""}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function DetailField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[10px] uppercase text-muted-foreground/60">{label}</p>
      <p className="text-foreground">{value}</p>
    </div>
  );
}

/* ── Create strategy modal ─────────────────────────────────────────── */

function CreateStrategyModal({
  paperPortfolios,
  existing,
  onClose,
  onCreated,
}: {
  // Every active paper portfolio the strategy can be attached to.
  // In create mode the user picks one; in edit mode the target is
  // fixed to the strategy's existing portfolio.
  paperPortfolios: Portfolio[];
  existing?: Strategy;
  onClose: () => void;
  onCreated: () => void;
}) {
  const isEdit = !!existing;
  // Default create-mode target: first paper portfolio; user can change.
  const [portfolioId, setPortfolioId] = useState<number>(
    existing?.portfolio_id ?? paperPortfolios[0]?.id ?? 0,
  );
  const [form, setForm] = useState(() => {
    if (!existing) return { ...DEFAULT_CREATE };
    // Rehydrate the form from the existing strategy. The override map
    // must be a full record with all signal-type keys nulled by default,
    // then overlaid with whatever's saved.
    const overridesMap: StrengthOverrideMap = { ...EMPTY_OVERRIDES };
    if (existing.signal_type_strength_overrides) {
      for (const [k, v] of Object.entries(existing.signal_type_strength_overrides)) {
        if (k in overridesMap) overridesMap[k as keyof StrengthOverrideMap] = v;
      }
    }
    return {
      name: existing.name,
      description: existing.description ?? "",
      signal_type: existing.signal_type,
      direction: (existing.direction === "neutral" ? null : existing.direction) as "bullish" | "bearish" | null,
      min_strength: existing.min_strength,
      signal_type_strength_overrides: overridesMap,
      tickers: existing.tickers ?? "",
      excluded_tickers: existing.excluded_tickers ?? "",
      allocation_mode: existing.allocation_mode as "fixed" | "percent",
      allocation_value: Number(existing.allocation_value),
      max_position_pct: Number(existing.max_position_pct),
      min_cash_reserve: Number(existing.min_cash_reserve),
      min_hold_days: existing.min_hold_days,
      base_hold_days: existing.base_hold_days,
      max_hold_days: existing.max_hold_days,
      exit_on_opposite_signal: existing.exit_on_opposite_signal,
      extend_on_continuing_signal: existing.extend_on_continuing_signal,
      stop_loss_pct: existing.stop_loss_pct,
    };
  });

  const save = useMutation({
    mutationFn: () => {
      const overrides: Record<string, number> = {};
      for (const [k, v] of Object.entries(form.signal_type_strength_overrides)) {
        if (typeof v === "number") overrides[k] = v;
      }
      const payload = {
        ...form,
        description: form.description.trim() || null,
        tickers: form.tickers.trim() || null,
        excluded_tickers: form.excluded_tickers.trim() || null,
        signal_type: form.signal_type || null,
        signal_type_strength_overrides: Object.keys(overrides).length ? overrides : null,
      };
      return isEdit
        ? strategiesApi.update(existing!.id, payload)
        : strategiesApi.create({ ...payload, portfolio_id: portfolioId });
    },
    onSuccess: () => onCreated(),
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? (isEdit ? "Failed to update strategy" : "Failed to create strategy");
      toast.error(typeof msg === "string" ? msg : (isEdit ? "Failed to update strategy" : "Failed to create strategy"));
    },
  });

  function update<K extends keyof typeof form>(key: K, value: typeof form[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" onClick={onClose}>
      <div
        className="w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-xl border border-border bg-card shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-border">
          <h2 className="font-semibold">{isEdit ? "Edit Strategy" : "New Strategy"}</h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="p-5 space-y-4">
          <Field
            label={isEdit ? "Paper portfolio (locked)" : "Paper portfolio"}
            required
          >
            <select
              value={portfolioId}
              onChange={(e) => setPortfolioId(parseInt(e.target.value))}
              disabled={isEdit}
              className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm disabled:opacity-60"
            >
              {paperPortfolios.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
            {!isEdit && paperPortfolios.length === 1 && (
              <p className="mt-1 text-[11px] text-muted-foreground">
                Create additional paper portfolios on the Portfolio page to run
                strategies in parallel with isolated equity curves.
              </p>
            )}
            {isEdit && (
              <p className="mt-1 text-[11px] text-muted-foreground">
                A strategy's portfolio is fixed after creation. Delete and
                recreate to move it (open positions must be closed first).
              </p>
            )}
          </Field>
          <Field label="Name" required>
            <input
              type="text"
              value={form.name}
              onChange={(e) => update("name", e.target.value)}
              placeholder="e.g. Strong Tech Bulls"
              className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm"
            />
          </Field>
          <Field label="Description (optional)">
            <input
              type="text"
              value={form.description}
              onChange={(e) => update("description", e.target.value)}
              placeholder="Notes about this strategy…"
              className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm"
            />
          </Field>

          <Section title="Signal filter">
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <Field label="Signal type">
                <select
                  value={form.signal_type ?? ""}
                  onChange={(e) => update("signal_type", e.target.value || null)}
                  className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm"
                >
                  <option value="">Any</option>
                  {SIGNAL_TYPES.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}
                </select>
              </Field>
              <Field label="Direction">
                <select
                  value={form.direction ?? ""}
                  onChange={(e) => update("direction", (e.target.value || null) as typeof form.direction)}
                  className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm"
                >
                  <option value="">Any</option>
                  <option value="bullish">Bullish (long)</option>
                  <option value="bearish">Bearish (exit-only)</option>
                </select>
              </Field>
              <Field label="Min strength">
                <select
                  value={form.min_strength}
                  onChange={(e) => update("min_strength", parseInt(e.target.value))}
                  className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm"
                >
                  {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}+ stars</option>)}
                </select>
              </Field>
            </div>
            <details className="rounded-md border border-border bg-input/40 px-3 py-2 text-sm">
              <summary className="cursor-pointer text-muted-foreground">
                Per-signal-type strength override (advanced)
              </summary>
              <p className="text-xs text-muted-foreground mt-2 mb-3">
                Override the global min strength on a per-provider basis. Signals from a provider
                below its override are excluded from the consolidated verdict. Leave any provider on
                <em> Use global</em> to fall back to the value above. Recommended cutoffs (from the
                2026-06-04 backtest) are shown next to each provider.
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {OVERRIDE_TYPES.map((t) => (
                  <label key={t.key} className="flex items-center gap-2 text-xs">
                    <span className="w-32 text-muted-foreground">
                      {t.label} <span className="opacity-60">(rec. {t.recommended})</span>
                    </span>
                    <select
                      value={form.signal_type_strength_overrides[t.key] ?? ""}
                      onChange={(e) => update("signal_type_strength_overrides", {
                        ...form.signal_type_strength_overrides,
                        [t.key]: e.target.value === "" ? null : parseInt(e.target.value),
                      })}
                      className="flex-1 px-2 py-1 rounded border border-border bg-input"
                    >
                      <option value="">Use global</option>
                      {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}+ stars</option>)}
                    </select>
                  </label>
                ))}
              </div>
            </details>
            <Field label="Restrict to tickers (comma-separated, optional)">
              <input
                type="text"
                value={form.tickers}
                onChange={(e) => update("tickers", e.target.value.toUpperCase())}
                placeholder="AAPL, NVDA, MSFT"
                className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm uppercase"
              />
            </Field>
            <Field label="Exclude tickers — blacklist (comma-separated, optional)">
              <input
                type="text"
                value={form.excluded_tickers}
                onChange={(e) => update("excluded_tickers", e.target.value.toUpperCase())}
                placeholder="APLD, SNDK, DRAM"
                className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm uppercase"
              />
              <p className="mt-1 text-[11px] text-muted-foreground">
                Consistent losers surfaced by the analysis script go here.
                Skips buys AND opposite-signal exits for these tickers.
              </p>
            </Field>
          </Section>

          <Section title="Allocation & risk">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Field label="Allocation mode">
                <select
                  value={form.allocation_mode}
                  onChange={(e) => update("allocation_mode", e.target.value as "fixed" | "percent")}
                  className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm"
                >
                  <option value="fixed">Fixed $ per trade</option>
                  <option value="percent">% of available cash</option>
                </select>
              </Field>
              <Field label={form.allocation_mode === "fixed" ? "Amount per trade ($)" : "% of cash per trade"}>
                <input
                  type="number"
                  value={form.allocation_value}
                  onChange={(e) => update("allocation_value", parseFloat(e.target.value) || 0)}
                  className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm"
                />
              </Field>
              <Field label="Max % per ticker">
                <input
                  type="number"
                  value={form.max_position_pct}
                  onChange={(e) => update("max_position_pct", parseFloat(e.target.value) || 0)}
                  className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm"
                />
              </Field>
              <Field label="Min cash reserve ($)">
                <input
                  type="number"
                  value={form.min_cash_reserve}
                  onChange={(e) => update("min_cash_reserve", parseFloat(e.target.value) || 0)}
                  className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm"
                />
              </Field>
              <Field label="Stop-loss % (negative, blank = off)">
                <input
                  type="number"
                  value={form.stop_loss_pct ?? ""}
                  onChange={(e) => {
                    const v = e.target.value;
                    update("stop_loss_pct", v === "" ? null : parseFloat(v));
                  }}
                  step="0.5"
                  max="0"
                  min="-100"
                  placeholder="e.g. -8"
                  className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm"
                />
                <p className="mt-1 text-[11px] text-muted-foreground">
                  Exit when unrealised P&L% breaches this. -8 = cut at -8%.
                  Checked every 15 min against cached prices.
                </p>
              </Field>
            </div>
          </Section>

          <Section title="Hold period (dynamic)">
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <Field label="Min hold (days)">
                <input
                  type="number"
                  value={form.min_hold_days}
                  onChange={(e) => update("min_hold_days", parseInt(e.target.value) || 0)}
                  className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm"
                />
              </Field>
              <Field label="Base hold (days)">
                <input
                  type="number"
                  value={form.base_hold_days}
                  onChange={(e) => update("base_hold_days", parseInt(e.target.value) || 1)}
                  className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm"
                />
              </Field>
              <Field label="Max hold (days)">
                <input
                  type="number"
                  value={form.max_hold_days}
                  onChange={(e) => update("max_hold_days", parseInt(e.target.value) || 1)}
                  className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm"
                />
              </Field>
            </div>
            <div className="space-y-2 text-xs">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={form.extend_on_continuing_signal}
                  onChange={(e) => update("extend_on_continuing_signal", e.target.checked)}
                  className="accent-primary"
                />
                <span className="text-muted-foreground">
                  Extend hold if same-direction signal renews (capped at max hold)
                </span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={form.exit_on_opposite_signal}
                  onChange={(e) => update("exit_on_opposite_signal", e.target.checked)}
                  className="accent-primary"
                />
                <span className="text-muted-foreground">
                  Exit early on opposite-direction signal (respecting min hold)
                </span>
              </label>
            </div>
          </Section>
        </div>
        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-border bg-secondary/20">
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded-md text-sm text-muted-foreground hover:text-foreground"
          >
            Cancel
          </button>
          <button
            onClick={() => save.mutate()}
            disabled={!form.name.trim() || save.isPending}
            className="px-4 py-1.5 rounded-md bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50"
          >
            {save.isPending ? "Saving…" : (isEdit ? "Save changes" : "Create Strategy")}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, required, children }: { label: string; required?: boolean; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs text-muted-foreground mb-1">
        {label}{required && <span className="text-red-500 ml-0.5">*</span>}
      </label>
      {children}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-3">
      <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">{title}</h3>
      {children}
    </div>
  );
}
