"use client";

import { useState, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { portfolioApi, marketApi, accountsApi } from "@/lib/api";
import { formatCurrency, formatPct, pnlColor, currencyLabel } from "@/lib/utils";
import { useCurrencyDisplay } from "@/hooks/useCurrencyDisplay";
import { CurrencySwitcher } from "@/components/ui/CurrencySwitcher";
import { PrivacyToggle } from "@/components/ui/PrivacyToggle";
import { InlineChart } from "@/components/charts/InlineChart";
import { AnimatedNumber } from "@/components/ui/AnimatedNumber";
import { usePrivacyStore } from "@/store/privacy";
import { useTradingModeStore } from "@/store/tradingMode";
import type { Portfolio, Position, Trade, Quote } from "@/types";
import { TickerLink } from "@/components/ui/TickerLink";
import { HalalBadge } from "@/components/ui/HalalBadge";
import { useHalalCompliance } from "@/hooks/useHalalCompliance";
import toast from "react-hot-toast";

const MASK = "••••••";

const ACTIONS = ["buy", "sell", "short", "cover"] as const;
const ASSET_TYPES = ["stock", "etf", "option", "crypto", "forex"] as const;

const EMPTY_TRADE = {
  ticker: "",
  action: "buy" as string,
  asset_type: "stock" as string,
  quantity: "",
  price: "",
  fees: "",
  currency: "USD",
  traded_at: new Date().toISOString().slice(0, 16),
  notes: "",
  // "" = auto (USD → SGD → EUR fallback chain); numeric string = explicit account id.
  account_id: "" as string,
};

type SortKey = "ticker" | "quantity" | "avg_cost" | "current_price" | "unrealized_pnl" | "unrealized_pnl_pct" | "day_change_pct";
type SortDir = "asc" | "desc";

function SortTh({
  label, col, sort, onSort,
}: { label: string; col: SortKey; sort: [SortKey, SortDir]; onSort: (c: SortKey) => void }) {
  const active = sort[0] === col;
  return (
    <th
      className="px-4 py-3 font-medium text-muted-foreground cursor-pointer select-none hover:text-foreground text-right"
      onClick={() => onSort(col)}
    >
      {label} {active ? (sort[1] === "asc" ? "↑" : "↓") : <span className="opacity-30">↕</span>}
    </th>
  );
}

export default function PortfolioPage() {
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newCurrency, setNewCurrency] = useState("USD");
  const [editingPortfolio, setEditingPortfolio] = useState<Portfolio | null>(null);
  const [editName, setEditName] = useState("");
  const [editCurrency, setEditCurrency] = useState("USD");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState<"positions" | "trades">("positions");
  const [expandedTicker, setExpandedTicker] = useState<string | null>(null);

  // Sort state for positions
  const [sort, setSort] = useState<[SortKey, SortDir]>(["ticker", "asc"]);

  // Add trade form
  const [showAddTrade, setShowAddTrade] = useState(false);
  const [tradeForm, setTradeForm] = useState({ ...EMPTY_TRADE });

  // Quick paper trade widget state
  const [paperTicker, setPaperTicker] = useState("");
  const [paperQty, setPaperQty] = useState("");

  // Edit trade state
  const [editingTrade, setEditingTrade] = useState<Trade | null>(null);
  const [editForm, setEditForm] = useState({ ...EMPTY_TRADE });

  const tradingMode = useTradingModeStore((s) => s.mode);

  const { data: allPortfolios = [], isLoading } = useQuery<Portfolio[]>({
    queryKey: ["portfolios"],
    queryFn: () => portfolioApi.list().then((r) => r.data),
    staleTime: 60_000,
  });

  // Filter portfolios by current trading mode — never combined.
  const portfolios = useMemo(
    () => allPortfolios.filter((p) =>
      tradingMode === "paper" ? p.broker === "paper" : p.broker !== "paper"
    ),
    [allPortfolios, tradingMode]
  );

  // Derive summary from the already-fetched list — avoids a second get_summary()
  // call with potentially different prices causing P&L to differ from the dashboard.
  const summary = portfolios.find((p) => p.id === selectedId);
  const isPaper = tradingMode === "paper";

  const { data: positions = [] } = useQuery<Position[]>({
    queryKey: ["positions", selectedId],
    queryFn: () => portfolioApi.positions(selectedId!).then((r) => r.data),
    enabled: !!selectedId,
    staleTime: 60_000,
  });

  const { data: trades = [] } = useQuery<Trade[]>({
    queryKey: ["trades", selectedId],
    queryFn: () => portfolioApi.trades(selectedId!).then((r) => r.data),
    enabled: !!selectedId && activeTab === "trades",
    staleTime: 60_000,
  });

  // Live quotes for change % column — runs in parallel with positions
  const tickers = useMemo(() => [...new Set(positions.map((p) => p.ticker))], [positions]);
  const halalByTicker = useHalalCompliance(tickers);
  const { data: quotes = [] } = useQuery<Quote[]>({
    queryKey: ["position-quotes", tickers],
    queryFn: () => marketApi.quotes(tickers).then((r) => r.data),
    enabled: tickers.length > 0,
    staleTime: 60_000,
    refetchInterval: 60_000,
  });
  const quoteMap = useMemo(
    () => Object.fromEntries(quotes.map((q) => [q.ticker, q])),
    [quotes]
  );

  // Recompute summary totals from live quotes when available
  const liveSummary = useMemo(() => {
    if (!summary || !positions.length || !quotes.length) return null;
    let totalValue = 0;
    let totalCost = 0;
    let dayChange = 0;
    for (const pos of positions) {
      const q = quoteMap[pos.ticker];
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
    const totalPnlPct = totalCost > 0 ? (totalPnl / totalCost) * 100 : null;
    const prevTotal = totalValue - dayChange;
    const dayChangePct = prevTotal > 0 ? (dayChange / prevTotal) * 100 : null;
    return { totalValue, totalCost, totalPnl, totalPnlPct, dayChange, dayChangePct };
  }, [summary, positions, quotes, quoteMap]);

  const { displayCurrency, setDisplayCurrency, rate, convert, base: baseCurrency } =
    useCurrencyDisplay(summary?.currency ?? "USD");

  const isPrivate = usePrivacyStore((s) => s.isPrivate);
  const mv = (val: string) => (isPrivate ? MASK : val);

  const updatePortfolioMutation = useMutation({
    mutationFn: () => portfolioApi.update(editingPortfolio!.id, {
      name: editName.trim() || undefined,
      currency: editCurrency.trim().toUpperCase() || undefined,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["portfolios"] });
      setEditingPortfolio(null);
      toast.success("Portfolio updated");
    },
    onError: () => toast.error("Failed to update portfolio"),
  });

  const createMutation = useMutation({
    mutationFn: () => portfolioApi.create({ name: newName.trim(), currency: newCurrency.trim().toUpperCase() || "USD" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["portfolios"] });
      setShowCreate(false);
      setNewName("");
      setNewCurrency("USD");
      toast.success("Portfolio created");
    },
  });

  // State for paper portfolio creation (only used when in paper mode and none exists)
  const [paperInitialCash, setPaperInitialCash] = useState("100000");

  // Edit-cash modal for the paper portfolio (initial_cash + cash_balance).
  // Pure number edits — does NOT clean up positions/trades/strategy_trades.
  // Useful when you want to top up virtual cash or move the starting anchor
  // for P&L calculation. For a real reset, build a separate /reset endpoint.
  const [showEditCash, setShowEditCash] = useState(false);
  const [editInitialCash, setEditInitialCash] = useState("");
  const [editCashBalance, setEditCashBalance] = useState("");

  const updateCashMutation = useMutation({
    mutationFn: () => {
      const payload: { initial_cash?: number; cash_balance?: number } = {};
      const ic = parseFloat(editInitialCash);
      const cb = parseFloat(editCashBalance);
      if (!Number.isNaN(ic) && ic >= 0) payload.initial_cash = ic;
      if (!Number.isNaN(cb) && cb >= 0) payload.cash_balance = cb;
      return portfolioApi.update(selectedId!, payload);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["portfolios"] });
      setShowEditCash(false);
      toast.success("Virtual cash updated");
    },
    onError: () => toast.error("Failed to update virtual cash"),
  });

  function openEditCash() {
    setEditInitialCash(String(summary?.initial_cash ?? ""));
    setEditCashBalance(String(summary?.cash_balance ?? ""));
    setShowEditCash(true);
  }

  const createPaperMutation = useMutation({
    mutationFn: () => portfolioApi.create({
      name: "Paper Trading",
      broker: "paper",
      currency: "USD",
      initial_cash: parseFloat(paperInitialCash) || 100000,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["portfolios"] });
      toast.success("Paper portfolio created — start trading!");
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? "Failed to create paper portfolio";
      toast.error(msg);
    },
  });

  const paperTradeMutation = useMutation({
    mutationFn: (data: { ticker: string; action: "buy" | "sell"; quantity: number }) =>
      portfolioApi.paperTrade(selectedId!, data),
    onSuccess: (_, vars) => {
      invalidateAll();
      setPaperTicker("");
      setPaperQty("");
      toast.success(`Paper ${vars.action.toUpperCase()} ${vars.quantity} ${vars.ticker.toUpperCase()} executed`);
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? "Paper trade failed";
      toast.error(msg);
    },
  });

  function submitPaperTrade(action: "buy" | "sell") {
    const ticker = paperTicker.trim().toUpperCase();
    const qty = parseFloat(paperQty);
    if (!ticker || !qty || qty <= 0) {
      toast.error("Enter a ticker and a positive quantity");
      return;
    }
    paperTradeMutation.mutate({ ticker, action, quantity: qty });
  }

  const addTradeMutation = useMutation({
    mutationFn: (data: object) => portfolioApi.addTrade(selectedId!, data),
    onSuccess: () => {
      invalidateAll();
      setShowAddTrade(false);
      setTradeForm({ ...EMPTY_TRADE });
      toast.success("Trade added");
    },
    onError: () => toast.error("Failed to add trade"),
  });

  const updateTradeMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: object }) =>
      portfolioApi.updateTrade(selectedId!, id, data),
    onSuccess: () => {
      invalidateAll();
      setEditingTrade(null);
      toast.success("Trade updated");
    },
    onError: () => toast.error("Failed to update trade"),
  });

  const deleteTradeMutation = useMutation({
    mutationFn: (tradeId: number) => portfolioApi.deleteTrade(selectedId!, tradeId),
    onSuccess: () => {
      invalidateAll();
      toast.success("Trade deleted");
    },
    onError: () => toast.error("Failed to delete trade"),
  });

  function invalidateAll() {
    qc.invalidateQueries({ queryKey: ["positions", selectedId] });
    qc.invalidateQueries({ queryKey: ["portfolios"] }); // summary is derived from this
    qc.invalidateQueries({ queryKey: ["trades", selectedId] });
  }

  // Auto-select first available portfolio in current mode; clear selection when
  // switching modes if the previously selected portfolio is no longer visible.
  const visibleIds = portfolios.map((p) => p.id);
  if (selectedId !== null && !visibleIds.includes(selectedId)) {
    setSelectedId(portfolios.length > 0 ? portfolios[0].id : null);
  } else if (!selectedId && portfolios.length > 0) {
    setSelectedId(portfolios[0].id);
  }

  function handleSortCol(col: SortKey) {
    setSort(([c, d]) => [col, c === col && d === "asc" ? "desc" : "asc"]);
  }

  const sortedPositions = [...positions].sort((a, b) => {
    const [col, dir] = sort;
    // day_change_pct lives in quoteMap, not position
    const getVal = (p: Position) =>
      col === "day_change_pct"
        ? (quoteMap[p.ticker]?.change_pct ?? -Infinity)
        : (p[col as keyof Position] ?? -Infinity);
    const av = getVal(a);
    const bv = getVal(b);
    const cmp = typeof av === "string" ? av.localeCompare(bv as string) : (av as number) - (bv as number);
    return dir === "asc" ? cmp : -cmp;
  });

  function buildTradePayload(form: typeof EMPTY_TRADE) {
    const payload: Record<string, unknown> = {
      ticker: form.ticker.trim().toUpperCase(),
      action: form.action,
      asset_type: form.asset_type,
      quantity: parseFloat(form.quantity),
      price: parseFloat(form.price),
      fees: form.fees ? parseFloat(form.fees) : 0,
      currency: form.currency || "USD",
      traded_at: new Date(form.traded_at).toISOString(),
      notes: form.notes || null,
    };
    // Only include account_id when the user picked a specific one; blank
    // means "use the fallback chain", which the backend treats as null.
    if (form.account_id) {
      payload.account_id = parseInt(form.account_id, 10);
    }
    return payload;
  }

  function handleSubmitTrade() {
    if (!tradeForm.ticker.trim() || !tradeForm.quantity || !tradeForm.price) {
      toast.error("Ticker, quantity and price are required");
      return;
    }
    addTradeMutation.mutate(buildTradePayload(tradeForm));
  }

  function handleEditOpen(trade: Trade) {
    setEditingTrade(trade);
    setEditForm({
      ticker: trade.ticker,
      action: trade.action,
      asset_type: trade.asset_type,
      quantity: String(trade.quantity),
      price: String(trade.price),
      fees: String(trade.fees),
      currency: trade.currency ?? "USD",
      traded_at: trade.traded_at.slice(0, 16),
      notes: trade.notes ?? "",
      account_id: trade.account_id != null ? String(trade.account_id) : "",
    });
  }

  function handleSubmitEdit() {
    if (!editingTrade) return;
    updateTradeMutation.mutate({
      id: editingTrade.id,
      data: {
        action: editForm.action,
        asset_type: editForm.asset_type,
        quantity: parseFloat(editForm.quantity),
        price: parseFloat(editForm.price),
        fees: editForm.fees ? parseFloat(editForm.fees) : 0,
        currency: editForm.currency || "USD",
        traded_at: new Date(editForm.traded_at).toISOString(),
        notes: editForm.notes || null,
      },
    });
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-bold">Portfolio</h1>
        <div className="flex flex-wrap gap-2 items-center">
          <PrivacyToggle />
          {selectedId && (
            <>
              <button
                onClick={invalidateAll}
                className="px-4 py-2 rounded-md bg-secondary text-sm font-medium hover:bg-secondary/80"
                title="Refresh prices"
              >
                ↻ Refresh
              </button>
              {!summary?.is_auto_managed && (
                <button
                  onClick={() => setShowAddTrade((v) => !v)}
                  className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90"
                >
                  + Add Trade
                </button>
              )}
            </>
          )}
          <button
            onClick={() => setShowCreate(true)}
            className="px-4 py-2 rounded-md bg-secondary text-sm font-medium hover:bg-secondary/80"
          >
            + Portfolio
          </button>
        </div>
      </div>

      {/* Create portfolio form */}
      {showCreate && (
        <div className="rounded-xl border border-border bg-card p-4 space-y-3">
          <h2 className="text-sm font-semibold">New Portfolio</h2>
          <div className="flex flex-wrap gap-3 items-end">
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Name *</label>
              <input
                autoFocus
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="e.g. Growth Portfolio"
                className="px-3 py-2 rounded-md border border-border bg-input text-sm w-56 focus:outline-none focus:ring-2 focus:ring-ring"
                onKeyDown={(e) => e.key === "Enter" && newName.trim() && createMutation.mutate()}
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Base Currency</label>
              <select
                value={newCurrency}
                onChange={(e) => setNewCurrency(e.target.value)}
                className="px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <option value="USD">USD — US Dollar</option>
                <option value="EUR">EUR — Euro</option>
                <option value="GBP">GBP — British Pound</option>
                <option value="JPY">JPY — Japanese Yen</option>
                <option value="CAD">CAD — Canadian Dollar</option>
                <option value="AUD">AUD — Australian Dollar</option>
                <option value="CHF">CHF — Swiss Franc</option>
                <option value="HKD">HKD — Hong Kong Dollar</option>
                <option value="SGD">SGD — Singapore Dollar</option>
                <option value="INR">INR — Indian Rupee</option>
              </select>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => createMutation.mutate()}
                disabled={!newName.trim() || createMutation.isPending}
                className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm disabled:opacity-50"
              >
                {createMutation.isPending ? "Creating…" : "Create"}
              </button>
              <button
                onClick={() => { setShowCreate(false); setNewName(""); setNewCurrency("USD"); }}
                className="px-4 py-2 rounded-md bg-secondary text-sm"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Edit Virtual Cash modal (paper portfolio only) */}
      {showEditCash && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="bg-card border border-border rounded-xl p-6 w-full max-w-sm mx-4 space-y-4">
            <div>
              <h2 className="text-sm font-semibold">Edit Virtual Cash</h2>
              <p className="text-xs text-muted-foreground mt-1">
                Adjust the starting anchor and/or the current balance. Existing
                positions and trades are <strong>not</strong> touched — for a
                clean wipe, you&apos;d want a full reset (not built yet).
              </p>
            </div>
            <div className="space-y-3">
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">Initial cash (anchor for P&L)</label>
                <input
                  type="number"
                  min="0"
                  step="any"
                  value={editInitialCash}
                  onChange={(e) => setEditInitialCash(e.target.value)}
                  className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">Current cash balance</label>
                <input
                  type="number"
                  min="0"
                  step="any"
                  value={editCashBalance}
                  onChange={(e) => setEditCashBalance(e.target.value)}
                  className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                />
                <p className="text-[11px] text-muted-foreground">
                  Setting this to a number different from the implied (initial − cost of
                  positions held) will make P&L look inconsistent until trades catch up.
                </p>
              </div>
            </div>
            <div className="flex gap-2 pt-1">
              <button
                onClick={() => updateCashMutation.mutate()}
                disabled={updateCashMutation.isPending}
                className="flex-1 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 disabled:opacity-50"
              >
                {updateCashMutation.isPending ? "Saving…" : "Save"}
              </button>
              <button
                onClick={() => setShowEditCash(false)}
                className="px-4 py-2 rounded-md bg-secondary text-sm hover:bg-secondary/80"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Edit portfolio modal */}
      {editingPortfolio && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="bg-card border border-border rounded-xl p-6 w-full max-w-sm mx-4 space-y-4">
            <h2 className="text-sm font-semibold">Edit Portfolio</h2>
            <div className="space-y-3">
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">Name</label>
                <input
                  autoFocus
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                  onKeyDown={(e) => e.key === "Enter" && updatePortfolioMutation.mutate()}
                />
              </div>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">Base Currency</label>
                <select
                  value={editCurrency}
                  onChange={(e) => setEditCurrency(e.target.value)}
                  className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  <option value="USD">USD — US Dollar</option>
                  <option value="EUR">EUR — Euro</option>
                  <option value="GBP">GBP — British Pound</option>
                  <option value="JPY">JPY — Japanese Yen</option>
                  <option value="CAD">CAD — Canadian Dollar</option>
                  <option value="AUD">AUD — Australian Dollar</option>
                  <option value="CHF">CHF — Swiss Franc</option>
                  <option value="HKD">HKD — Hong Kong Dollar</option>
                  <option value="SGD">SGD — Singapore Dollar</option>
                  <option value="INR">INR — Indian Rupee</option>
                </select>
              </div>
            </div>
            <div className="flex gap-2 pt-1">
              <button
                onClick={() => updatePortfolioMutation.mutate()}
                disabled={updatePortfolioMutation.isPending}
                className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50"
              >
                {updatePortfolioMutation.isPending ? "Saving…" : "Save"}
              </button>
              <button
                onClick={() => setEditingPortfolio(null)}
                className="px-4 py-2 rounded-md bg-secondary text-sm"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Add trade form */}
      {showAddTrade && (
        <TradeForm
          form={tradeForm}
          setForm={setTradeForm}
          onSubmit={handleSubmitTrade}
          onCancel={() => { setShowAddTrade(false); setTradeForm({ ...EMPTY_TRADE }); }}
          isPending={addTradeMutation.isPending}
          title="New Trade"
        />
      )}

      {/* Edit trade modal */}
      {editingTrade && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="bg-card border border-border rounded-xl p-6 w-full max-w-lg mx-4">
            <h2 className="text-sm font-semibold mb-4">
              Edit Trade — {editingTrade.ticker}
            </h2>
            <TradeForm
              form={editForm}
              setForm={setEditForm}
              onSubmit={handleSubmitEdit}
              onCancel={() => setEditingTrade(null)}
              isPending={updateTradeMutation.isPending}
              title=""
              hideTicker
            />
          </div>
        </div>
      )}

      {isLoading && <p className="text-muted-foreground text-sm">Loading…</p>}

      {portfolios.length === 0 && !isLoading && !isPaper && (
        <div className="rounded-xl border border-border bg-card p-8 text-center">
          <p className="text-muted-foreground">No portfolios yet.</p>
          <button
            onClick={() => { setShowCreate(true); }}
            className="mt-3 text-sm underline underline-offset-2"
          >
            Create your first portfolio
          </button>
        </div>
      )}

      {portfolios.length === 0 && !isLoading && isPaper && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-8 space-y-4">
          <div>
            <p className="text-foreground font-medium">No paper trading portfolio yet</p>
            <p className="text-sm text-muted-foreground mt-1">
              Create one to start executing virtual trades on signals. Your real
              portfolios are completely unaffected.
            </p>
          </div>
          <div className="flex items-end gap-3">
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Initial cash (USD)</label>
              <input
                type="number"
                value={paperInitialCash}
                onChange={(e) => setPaperInitialCash(e.target.value)}
                className="px-3 py-2 rounded-md border border-border bg-input text-sm w-40"
              />
            </div>
            <button
              onClick={() => createPaperMutation.mutate()}
              disabled={createPaperMutation.isPending}
              className="px-4 py-2 rounded-md bg-amber-500 text-amber-950 text-sm font-medium disabled:opacity-50"
            >
              {createPaperMutation.isPending ? "Creating…" : "Create Paper Portfolio"}
            </button>
          </div>
        </div>
      )}

      {portfolios.length > 0 && (
        <>
          {/* Portfolio tabs */}
          <div className="flex gap-2 flex-wrap items-center">
            {portfolios.map((p) => (
              <div key={p.id} className="flex items-center gap-1">
                <button
                  onClick={() => setSelectedId(p.id)}
                  className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                    selectedId === p.id
                      ? "bg-secondary text-foreground"
                      : "text-muted-foreground hover:bg-secondary/50"
                  }`}
                >
                  {p.name}
                  {p.currency && p.currency !== "USD" && (
                    <span className="ml-1.5 text-xs opacity-60">{p.currency}</span>
                  )}
                </button>
                {selectedId === p.id && (
                  <button
                    onClick={() => {
                      setEditingPortfolio(p);
                      setEditName(p.name);
                      setEditCurrency(p.currency ?? "USD");
                    }}
                    className="p-1.5 rounded-md text-muted-foreground hover:bg-secondary/60 hover:text-foreground transition-colors"
                    title="Edit portfolio"
                  >
                    ✎
                  </button>
                )}
              </div>
            ))}
          </div>

          {/* Summary cards */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <p className="text-xs text-muted-foreground">Totals converted to</p>
              <CurrencySwitcher
                base={baseCurrency}
                display={displayCurrency}
                rate={rate}
                onChange={setDisplayCurrency}
              />
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-4">
              <SummaryCard
                label="Total Portfolio Value"
                value={mv(formatCurrency(convert(liveSummary?.totalValue ?? summary?.total_value), displayCurrency))}
                note={isPrivate ? undefined : currencyLabel(displayCurrency)}
              />
              <SummaryCard
                label="Total Cost"
                value={mv(formatCurrency(convert(liveSummary?.totalCost ?? summary?.total_cost), displayCurrency))}
                note={isPrivate ? undefined : currencyLabel(displayCurrency)}
              />
              <SummaryCard
                label="Unrealized P&L"
                value={mv(formatCurrency(convert(liveSummary?.totalPnl ?? summary?.total_pnl), displayCurrency))}
                valueClass={isPrivate ? undefined : pnlColor(liveSummary?.totalPnl ?? summary?.total_pnl)}
                note={isPrivate ? undefined : currencyLabel(displayCurrency)}
              />
              <SummaryCard
                label="Return"
                value={formatPct(liveSummary?.totalPnlPct ?? summary?.total_pnl_pct)}
                valueClass={pnlColor(liveSummary?.totalPnlPct ?? summary?.total_pnl_pct)}
              />
              <SummaryCard
                label="Today's Change"
                value={mv(formatCurrency(convert(liveSummary?.dayChange ?? summary?.day_change), displayCurrency))}
                valueClass={isPrivate ? undefined : pnlColor(liveSummary?.dayChange ?? summary?.day_change)}
                note={isPrivate || (liveSummary?.dayChangePct ?? summary?.day_change_pct) == null ? undefined : formatPct(liveSummary?.dayChangePct ?? summary?.day_change_pct)}
              />
            </div>
          </div>

          {/* Auto-managed portfolio banner: explain why manual actions are hidden */}
          {summary?.is_auto_managed && (
            <div className="rounded-xl border border-blue-500/30 bg-blue-500/5 p-3 text-sm text-blue-300">
              This portfolio is managed by an auto-trader strategy. Manual trades
              are disabled to keep the strategy ledger in sync. Pause or delete
              the strategy if you need to record trades by hand.
            </div>
          )}

          {/* Paper trading: cash balance + (optional) quick trade widget */}
          {isPaper && summary && (
            <div className={`grid grid-cols-1 gap-4 ${summary.is_auto_managed ? "" : "md:grid-cols-3"}`}>
              <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4">
                <div className="flex items-start justify-between gap-2">
                  <p className="text-xs text-amber-500 uppercase tracking-wider font-medium">Virtual Cash</p>
                  <button
                    onClick={openEditCash}
                    className="text-[10px] text-amber-500/70 hover:text-amber-500 underline-offset-2 hover:underline"
                    title="Edit initial cash and current balance (numbers only — no trade/position cleanup)"
                  >
                    Edit
                  </button>
                </div>
                <p className="text-2xl font-bold mt-1">
                  {mv(formatCurrency(summary.cash_balance ?? 0, "USD"))}
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  of {formatCurrency(summary.initial_cash ?? 0, "USD")} initial
                </p>
              </div>
              {!summary.is_auto_managed && (
              <div className="md:col-span-2 rounded-xl border border-border bg-card p-4">
                <p className="text-xs text-muted-foreground uppercase tracking-wider font-medium mb-3">Quick Trade</p>
                <div className="flex flex-wrap items-end gap-2">
                  <div className="flex-1 min-w-[120px]">
                    <label className="block text-xs text-muted-foreground mb-1">Ticker</label>
                    <input
                      type="text"
                      value={paperTicker}
                      onChange={(e) => setPaperTicker(e.target.value.toUpperCase())}
                      placeholder="AAPL"
                      className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm uppercase"
                    />
                  </div>
                  <div className="flex-1 min-w-[100px]">
                    <label className="block text-xs text-muted-foreground mb-1">Quantity</label>
                    <input
                      type="number"
                      value={paperQty}
                      onChange={(e) => setPaperQty(e.target.value)}
                      placeholder="10"
                      className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm"
                    />
                  </div>
                  <button
                    onClick={() => submitPaperTrade("buy")}
                    disabled={paperTradeMutation.isPending}
                    className="px-4 py-2 rounded-md bg-emerald-500 text-emerald-950 text-sm font-medium disabled:opacity-50"
                  >
                    Buy
                  </button>
                  <button
                    onClick={() => submitPaperTrade("sell")}
                    disabled={paperTradeMutation.isPending}
                    className="px-4 py-2 rounded-md bg-red-500 text-red-50 text-sm font-medium disabled:opacity-50"
                  >
                    Sell
                  </button>
                </div>
                <p className="text-xs text-muted-foreground mt-2">Market order at the current quote price.</p>
              </div>
              )}
            </div>
          )}

          {/* Positions / Trades tabs */}
          <div className="flex gap-1 border-b border-border">
            {(["positions", "trades"] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-4 py-2 text-sm font-medium capitalize border-b-2 transition-colors ${
                  activeTab === tab
                    ? "border-primary text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                }`}
              >
                {tab}
              </button>
            ))}
          </div>

          {/* Positions table */}
          {activeTab === "positions" && (
            <div className="rounded-xl border border-border overflow-hidden overflow-x-auto">
              <table className="w-full text-sm min-w-[640px]">
                <thead>
                  <tr className="border-b border-border bg-secondary/50">
                    <th
                      className="text-left px-4 py-3 font-medium text-muted-foreground cursor-pointer select-none hover:text-foreground"
                      onClick={() => handleSortCol("ticker")}
                    >
                      Ticker {sort[0] === "ticker" ? (sort[1] === "asc" ? "↑" : "↓") : <span className="opacity-30">↕</span>}
                    </th>
                    <SortTh label="Qty" col="quantity" sort={sort} onSort={handleSortCol} />
                    <SortTh label="Avg Cost" col="avg_cost" sort={sort} onSort={handleSortCol} />
                    <SortTh label="Current" col="current_price" sort={sort} onSort={handleSortCol} />
                    <SortTh label="Day %" col="day_change_pct" sort={sort} onSort={handleSortCol} />
                    <SortTh label="P&L" col="unrealized_pnl" sort={sort} onSort={handleSortCol} />
                    <SortTh label="Return" col="unrealized_pnl_pct" sort={sort} onSort={handleSortCol} />
                  </tr>
                </thead>
                <tbody>
                  {sortedPositions.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="px-4 py-8 text-center text-muted-foreground">
                        No positions yet. Add trades to see them here.
                      </td>
                    </tr>
                  ) : (
                    sortedPositions.flatMap((pos) => {
                      const q = quoteMap[pos.ticker];
                      const isExpanded = expandedTicker === pos.ticker;
                      return [
                        <tr key={pos.id} className={`border-b border-border/50 hover:bg-secondary/20 ${isExpanded ? "bg-secondary/10" : ""}`}>
                          <td className="px-4 py-3 font-semibold">
                            <div className="flex items-center gap-1.5">
                              <button
                                onClick={() => setExpandedTicker(isExpanded ? null : pos.ticker)}
                                className="text-xs text-muted-foreground/50 hover:text-foreground transition-colors"
                                aria-label={isExpanded ? "Collapse" : "Expand"}
                              >
                                <span className="inline-block transition-transform duration-200" style={{ transform: isExpanded ? "rotate(90deg)" : "rotate(0deg)" }}>
                                  ▶
                                </span>
                              </button>
                              <TickerLink ticker={pos.ticker} />
                              <HalalBadge compliance={halalByTicker[pos.ticker]} />
                              {pos.currency && pos.currency !== "USD" && (
                                <span className="text-xs px-1.5 py-0.5 rounded bg-secondary text-muted-foreground">
                                  {pos.currency}
                                </span>
                              )}
                            </div>
                          </td>
                          <td className="px-4 py-3 text-right">{mv(String(pos.quantity))}</td>
                          <td className="px-4 py-3 text-right">{formatCurrency(pos.avg_cost, pos.currency)}</td>
                          <td className="px-4 py-3 text-right">{formatCurrency(q?.price ?? pos.current_price, pos.currency)}</td>
                          <td className={`px-4 py-3 text-right ${isPrivate ? "" : pnlColor(q?.change_pct)}`}>
                            {q ? formatPct(q.change_pct) : "—"}
                          </td>
                          <td className={`px-4 py-3 text-right ${isPrivate ? "" : pnlColor(q ? (q.price - pos.avg_cost) * pos.quantity : pos.unrealized_pnl)}`}>
                            {mv(formatCurrency(q ? (q.price - pos.avg_cost) * pos.quantity : pos.unrealized_pnl, pos.currency))}
                          </td>
                          <td className={`px-4 py-3 text-right ${isPrivate ? "" : pnlColor(q ? (q.price - pos.avg_cost) / pos.avg_cost * 100 : pos.unrealized_pnl_pct)}`}>
                            {formatPct(q ? (q.price - pos.avg_cost) / pos.avg_cost * 100 : pos.unrealized_pnl_pct)}
                          </td>
                        </tr>,
                        isExpanded && (
                          <tr key={`${pos.ticker}-chart`}>
                            <td colSpan={7} className="p-0 border-b border-border">
                              <InlineChart ticker={pos.ticker} quote={q} />
                            </td>
                          </tr>
                        ),
                      ].filter(Boolean);
                    })
                  )}
                </tbody>
              </table>
            </div>
          )}

          {/* Trades table */}
          {activeTab === "trades" && (
            <div className="rounded-xl border border-border overflow-hidden overflow-x-auto">
              <table className="w-full text-sm min-w-[640px]">
                <thead>
                  <tr className="border-b border-border bg-secondary/50">
                    <th className="text-left px-4 py-3 font-medium text-muted-foreground">Date</th>
                    <th className="text-left px-4 py-3 font-medium text-muted-foreground">Ticker</th>
                    <th className="text-left px-4 py-3 font-medium text-muted-foreground">Action</th>
                    <th className="text-right px-4 py-3 font-medium text-muted-foreground">Qty</th>
                    <th className="text-right px-4 py-3 font-medium text-muted-foreground">Price</th>
                    <th className="text-right px-4 py-3 font-medium text-muted-foreground">Fees</th>
                    <th className="text-right px-4 py-3 font-medium text-muted-foreground">Total</th>
                    <th className="px-4 py-3" />
                  </tr>
                </thead>
                <tbody>
                  {trades.length === 0 ? (
                    <tr>
                      <td colSpan={8} className="px-4 py-8 text-center text-muted-foreground">
                        No trades yet.
                      </td>
                    </tr>
                  ) : (
                    trades.map((t) => {
                      const actionColor =
                        t.action === "buy" ? "text-emerald-500" :
                        t.action === "sell" ? "text-red-500" :
                        t.action === "short" ? "text-orange-500" : "text-blue-500";
                      return (
                        <tr key={t.id} className="border-b border-border/50 hover:bg-secondary/20">
                          <td className="px-4 py-3 text-muted-foreground text-xs">
                            {new Date(t.traded_at).toLocaleDateString()}
                          </td>
                          <td className="px-4 py-3 font-semibold">
                            {t.ticker}
                            {t.currency && t.currency !== "USD" && (
                              <span className="ml-1.5 text-xs px-1.5 py-0.5 rounded bg-secondary text-muted-foreground">
                                {t.currency}
                              </span>
                            )}
                          </td>
                          <td className={`px-4 py-3 font-medium uppercase text-xs ${actionColor}`}>
                            {t.action}
                          </td>
                          <td className="px-4 py-3 text-right">{mv(String(t.quantity))}</td>
                          <td className="px-4 py-3 text-right">{formatCurrency(t.price, t.currency)}</td>
                          <td className="px-4 py-3 text-right text-muted-foreground">
                            {t.fees ? formatCurrency(t.fees, t.currency) : "—"}
                          </td>
                          <td className="px-4 py-3 text-right">
                            {mv(formatCurrency(t.quantity * t.price, t.currency))}
                          </td>
                          <td className="px-4 py-3 text-right">
                            {summary?.is_auto_managed ? (
                              <span className="text-xs text-muted-foreground/60 italic">auto</span>
                            ) : (
                              <div className="flex gap-2 justify-end">
                                <button
                                  onClick={() => handleEditOpen(t)}
                                  className="text-xs px-2 py-1 rounded bg-secondary hover:bg-secondary/80"
                                >
                                  Edit
                                </button>
                                <button
                                  onClick={() => {
                                    if (confirm(`Delete this ${t.action} trade for ${t.ticker}?`))
                                      deleteTradeMutation.mutate(t.id);
                                  }}
                                  disabled={deleteTradeMutation.isPending}
                                  className="text-xs px-2 py-1 rounded bg-destructive/20 text-red-400 hover:bg-destructive/40 disabled:opacity-50"
                                >
                                  Delete
                                </button>
                              </div>
                            )}
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function TradeForm({
  form, setForm, onSubmit, onCancel, isPending, title, hideTicker,
}: {
  form: typeof EMPTY_TRADE;
  setForm: React.Dispatch<React.SetStateAction<typeof EMPTY_TRADE>>;
  onSubmit: () => void;
  onCancel: () => void;
  isPending: boolean;
  title: string;
  hideTicker?: boolean;
}) {
  // Cash accounts for the Funding-account dropdown. Cached for 5 min — the
  // list rarely changes mid-session, and balances aren't worth re-polling
  // every keystroke. The displayed balance per account is informational
  // only; the backend re-checks at submit time.
  const { data: accounts = [] } = useQuery<import("@/types").Account[]>({
    queryKey: ["accounts"],
    queryFn: () => accountsApi.list().then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  });

  async function detectCurrency(ticker: string) {
    const t = ticker.trim().toUpperCase();
    if (!t) return;
    try {
      const res = await marketApi.currency(t);
      setForm((f) => ({ ...f, currency: res.data.currency }));
    } catch { /* ignore */ }
  }

  // Format each account's dropdown label. Shows the trade-currency
  // balance if present, otherwise notes that FX-within-account will be
  // used to cover the trade. The "→ CCY" suffix tells you which currency
  // sell proceeds will be credited as.
  function accountLabel(acct: import("@/types").Account): string {
    const ccy = form.currency || "USD";
    const bal = acct.balances?.find((b) => b.currency === ccy);
    const primary = acct.primary_currency ?? "USD";
    const proceedsSuffix = primary !== ccy ? ` · sells → ${primary}` : "";
    if (bal) {
      return `${acct.name} — ${formatCurrency(bal.balance, ccy)} ${ccy}${proceedsSuffix}`;
    }
    return `${acct.name} — no ${ccy} balance (will FX)${proceedsSuffix}`;
  }

  return (
    <div className="space-y-4">
      {title && <h2 className="text-sm font-semibold">{title}</h2>}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        {!hideTicker && (
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Ticker *</label>
            <input
              autoFocus
              value={form.ticker}
              onChange={(e) => setForm((f) => ({ ...f, ticker: e.target.value.toUpperCase() }))}
              onBlur={(e) => detectCurrency(e.target.value)}
              placeholder="e.g. MBG.DE"
              className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
        )}
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Action *</label>
          <select
            value={form.action}
            onChange={(e) => setForm((f) => ({ ...f, action: e.target.value }))}
            className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {ACTIONS.map((a) => (
              <option key={a} value={a}>{a.charAt(0).toUpperCase() + a.slice(1)}</option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Asset Type</label>
          <select
            value={form.asset_type}
            onChange={(e) => setForm((f) => ({ ...f, asset_type: e.target.value }))}
            className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {ASSET_TYPES.map((a) => (
              <option key={a} value={a}>{a.charAt(0).toUpperCase() + a.slice(1)}</option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Quantity *</label>
          <input
            type="number" min="0" step="any"
            value={form.quantity}
            onChange={(e) => setForm((f) => ({ ...f, quantity: e.target.value }))}
            placeholder="e.g. 10"
            className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Price *</label>
          <input
            type="number" min="0" step="any"
            value={form.price}
            onChange={(e) => setForm((f) => ({ ...f, price: e.target.value }))}
            placeholder="e.g. 182.50"
            className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Fees</label>
          <input
            type="number" min="0" step="any"
            value={form.fees}
            onChange={(e) => setForm((f) => ({ ...f, fees: e.target.value }))}
            placeholder="e.g. 1.00"
            className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Currency</label>
          <input
            value={form.currency}
            onChange={(e) => setForm((f) => ({ ...f, currency: e.target.value.toUpperCase() }))}
            placeholder="USD"
            maxLength={10}
            className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <div className="space-y-1 sm:col-span-2">
          <label className="text-xs text-muted-foreground">Funding account</label>
          <select
            value={form.account_id}
            onChange={(e) => setForm((f) => ({ ...f, account_id: e.target.value }))}
            className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <option value="">Auto (USD → SGD → EUR fallback chain)</option>
            {accounts.map((acct) => (
              <option key={acct.id} value={String(acct.id)}>
                {accountLabel(acct)}
              </option>
            ))}
          </select>
          {form.account_id && (
            <p className="text-[11px] text-muted-foreground">
              Buys debit {form.currency || "USD"} from this account first; if short,
              FX from its other currencies. Rejected only if the whole account can&apos;t cover.
              Sells credit proceeds, FX-converted to the account&apos;s primary currency.
            </p>
          )}
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Date &amp; Time *</label>
          <input
            type="datetime-local"
            value={form.traded_at}
            onChange={(e) => setForm((f) => ({ ...f, traded_at: e.target.value }))}
            className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <div className="space-y-1 sm:col-span-2">
          <label className="text-xs text-muted-foreground">Notes</label>
          <input
            value={form.notes}
            onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
            placeholder="Optional notes"
            className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
      </div>
      <div className="flex gap-2 pt-1">
        <button
          onClick={onSubmit}
          disabled={isPending}
          className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50"
        >
          {isPending ? "Saving…" : "Save"}
        </button>
        <button onClick={onCancel} className="px-4 py-2 rounded-md bg-secondary text-sm">
          Cancel
        </button>
      </div>
    </div>
  );
}

function SummaryCard({ label, value, valueClass, note }: { label: string; value: string; valueClass?: string; note?: string }) {
  return (
    <div className="rounded-xl border border-border bg-card p-4 min-w-0">
      <div className="flex items-center justify-between gap-1 min-w-0">
        <p className="text-xs text-muted-foreground truncate">{label}</p>
        {note && <span className="text-xs text-muted-foreground/60 shrink-0">{note}</span>}
      </div>
      <p className={`text-base sm:text-xl font-bold mt-1 truncate ${valueClass ?? ""}`}>
        <AnimatedNumber value={value} />
      </p>
    </div>
  );
}
