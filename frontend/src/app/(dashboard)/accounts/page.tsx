"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { accountsApi } from "@/lib/api";
import { formatCurrency } from "@/lib/utils";
import { useCurrencyDisplay } from "@/hooks/useCurrencyDisplay";
import { CurrencySwitcher } from "@/components/ui/CurrencySwitcher";
import { usePrivacyStore } from "@/store/privacy";
import { PrivacyToggle } from "@/components/ui/PrivacyToggle";
import type { Account, AccountDetail, AccountTransaction } from "@/types";
import toast from "react-hot-toast";

const CURRENCIES =["USD", "GBP", "EUR", "JPY", "CAD", "AUD", "CHF", "SGD", "HKD", "NOK", "SEK", "DKK"];

export default function AccountsPage() {
  const qc = useQueryClient();
  const isPrivate = usePrivacyStore((s) => s.isPrivate);
  const { displayCurrency, setDisplayCurrency, rate, convert, base } = useCurrencyDisplay("USD");

  const [showNewAccount, setShowNewAccount] = useState(false);
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null);
  const [txModal, setTxModal] = useState<
    | { mode: "create"; type: "deposit" | "withdraw"; accountId: number }
    | { mode: "edit"; accountId: number; transaction: AccountTransaction }
    | null
  >(null);

  const { data: accounts = [] } = useQuery<Account[]>({
    queryKey: ["accounts"],
    queryFn: () => accountsApi.list().then((r) => r.data),
  });

  const { data: detail } = useQuery<AccountDetail>({
    queryKey: ["account", selectedAccountId],
    queryFn: () => accountsApi.get(selectedAccountId!).then((r) => r.data),
    enabled: !!selectedAccountId,
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h1 className="text-2xl font-bold">Accounts</h1>
        <div className="flex items-center gap-2 flex-wrap">
          <PrivacyToggle />
          <CurrencySwitcher base={base} display={displayCurrency} rate={rate} onChange={setDisplayCurrency} />
          <button
            onClick={() => setShowNewAccount(true)}
            className="px-3 py-1.5 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:opacity-90"
          >
            + New Account
          </button>
        </div>
      </div>

      {/* Account cards */}
      {accounts.length === 0 ? (
        <div className="rounded-xl border border-border bg-card p-8 text-center text-muted-foreground">
          <p className="font-medium mb-1">No accounts yet</p>
          <p className="text-sm">Create an account to track cash balances across currencies.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {accounts.map((acct) => (
            <AccountCard
              key={acct.id}
              account={acct}
              isPrivate={isPrivate}
              convert={convert}
              displayCurrency={displayCurrency}
              onSelect={() => setSelectedAccountId(acct.id === selectedAccountId ? null : acct.id)}
              selected={acct.id === selectedAccountId}
              onDeposit={() => setTxModal({ mode: "create", type: "deposit", accountId: acct.id })}
              onWithdraw={() => setTxModal({ mode: "create", type: "withdraw", accountId: acct.id })}
              onDelete={async () => {
                if (!confirm(`Delete account "${acct.name}"?`)) return;
                await accountsApi.delete(acct.id);
                qc.invalidateQueries({ queryKey: ["accounts"] });
                qc.invalidateQueries({ queryKey: ["liquidity"] });
                if (selectedAccountId === acct.id) setSelectedAccountId(null);
                toast.success("Account deleted");
              }}
            />
          ))}
        </div>
      )}

      {/* Transaction history for selected account */}
      {selectedAccountId && detail && (
        <section>
          <h2 className="text-lg font-semibold mb-3">
            Transactions — {detail.name}
          </h2>
          {detail.transactions.length === 0 ? (
            <p className="text-sm text-muted-foreground">No transactions yet.</p>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-border">
              <table className="w-full text-sm min-w-[500px]">
                <thead>
                  <tr className="border-b border-border bg-card">
                    <th className="text-left px-4 py-2 text-muted-foreground font-medium">Date</th>
                    <th className="text-left px-4 py-2 text-muted-foreground font-medium">Type</th>
                    <th className="text-right px-4 py-2 text-muted-foreground font-medium">Amount</th>
                    <th className="text-left px-4 py-2 text-muted-foreground font-medium">Notes</th>
                    <th className="text-right px-4 py-2" />
                  </tr>
                </thead>
                <tbody>
                  {detail.transactions.map((tx) => (
                    <TransactionRow
                      key={tx.id}
                      tx={tx}
                      isPrivate={isPrivate}
                      onEdit={() => setTxModal({ mode: "edit", accountId: detail.id, transaction: tx })}
                      onDelete={async () => {
                        if (!confirm(`Delete this ${tx.transaction_type} of ${tx.amount} ${tx.currency}?`)) return;
                        try {
                          await accountsApi.deleteTransaction(detail.id, tx.id);
                          qc.invalidateQueries({ queryKey: ["accounts"] });
                          qc.invalidateQueries({ queryKey: ["account", detail.id] });
                          qc.invalidateQueries({ queryKey: ["liquidity"] });
                          toast.success("Transaction deleted");
                        } catch (err) {
                          // axios error shape — backend returns 400 if delete
                          // would push balance negative.
                          const e = err as { response?: { data?: { detail?: string } } };
                          toast.error(e?.response?.data?.detail ?? "Failed to delete transaction");
                        }
                      }}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {/* New account modal */}
      {showNewAccount && (
        <NewAccountModal
          onClose={() => setShowNewAccount(false)}
          onCreated={() => {
            qc.invalidateQueries({ queryKey: ["accounts"] });
            qc.invalidateQueries({ queryKey: ["liquidity"] });
            setShowNewAccount(false);
          }}
        />
      )}

      {/* Deposit / withdraw / edit modal */}
      {txModal && (
        <TransactionModal
          {...(txModal.mode === "edit"
            ? { mode: "edit", accountId: txModal.accountId, transaction: txModal.transaction }
            : { mode: "create", accountId: txModal.accountId, type: txModal.type })}
          onClose={() => setTxModal(null)}
          onDone={() => {
            qc.invalidateQueries({ queryKey: ["accounts"] });
            qc.invalidateQueries({ queryKey: ["account", txModal.accountId] });
            qc.invalidateQueries({ queryKey: ["liquidity"] });
            setTxModal(null);
          }}
        />
      )}
    </div>
  );
}

// ── Account card ──────────────────────────────────────────────────────────────

function AccountCard({
  account, isPrivate, convert, displayCurrency,
  onSelect, selected, onDeposit, onWithdraw, onDelete,
}: {
  account: Account;
  isPrivate: boolean;
  convert: (v: number | null | undefined) => number | null;
  displayCurrency: string;
  onSelect: () => void;
  selected: boolean;
  onDeposit: () => void;
  onWithdraw: () => void;
  onDelete: () => void;
}) {
  const mv = (v: string) => (isPrivate ? "••••••" : v);

  return (
    <div
      className={`rounded-xl border bg-card p-4 space-y-3 cursor-pointer transition-colors ${
        selected ? "border-primary" : "border-border hover:border-primary/50"
      }`}
      onClick={onSelect}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-semibold truncate">{account.name}</p>
          {account.description && (
            <p className="text-xs text-muted-foreground truncate">{account.description}</p>
          )}
        </div>
        <button
          onClick={(e) => { e.stopPropagation(); onDelete(); }}
          className="text-muted-foreground hover:text-red-400 transition-colors text-xs shrink-0"
          title="Delete account"
        >
          ✕
        </button>
      </div>

      {/* Currency balances */}
      <div className="space-y-1">
        {account.balances.length === 0 ? (
          <p className="text-xs text-muted-foreground">No balance yet</p>
        ) : (
          account.balances.map((bal) => (
            <div key={bal.id} className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground text-xs font-medium w-10">{bal.currency}</span>
              <span className="font-medium tabular-nums">
                {mv(formatCurrency(bal.balance, bal.currency))}
              </span>
            </div>
          ))
        )}
      </div>

      {/* Action buttons */}
      <div className="flex gap-2 pt-1" onClick={(e) => e.stopPropagation()}>
        <button
          onClick={onDeposit}
          className="flex-1 px-2 py-1.5 rounded-md bg-emerald-600/20 text-emerald-400 hover:bg-emerald-600/30 text-xs font-medium transition-colors"
        >
          Deposit
        </button>
        <button
          onClick={onWithdraw}
          className="flex-1 px-2 py-1.5 rounded-md bg-red-600/20 text-red-400 hover:bg-red-600/30 text-xs font-medium transition-colors"
        >
          Withdraw
        </button>
      </div>
    </div>
  );
}

// ── Transaction row ───────────────────────────────────────────────────────────

function TransactionRow({
  tx, isPrivate, onEdit, onDelete,
}: {
  tx: AccountTransaction;
  isPrivate: boolean;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const isDeposit = tx.transaction_type === "deposit";
  return (
    <tr className="border-b border-border last:border-0 hover:bg-secondary/20 transition-colors">
      <td className="px-4 py-2 text-muted-foreground whitespace-nowrap">
        {new Date(tx.transacted_at).toLocaleDateString()}
      </td>
      <td className="px-4 py-2">
        <span className={`text-xs font-medium ${isDeposit ? "text-emerald-400" : "text-red-400"}`}>
          {isDeposit ? "Deposit" : "Withdrawal"}
        </span>
      </td>
      <td className={`px-4 py-2 text-right font-medium tabular-nums ${isDeposit ? "text-emerald-400" : "text-red-400"}`}>
        {isPrivate ? "••••••" : `${isDeposit ? "+" : "−"}${formatCurrency(tx.amount, tx.currency)}`}
        <span className="text-xs text-muted-foreground ml-1">{tx.currency}</span>
      </td>
      <td className="px-4 py-2 text-muted-foreground text-xs truncate max-w-[200px]">
        {tx.notes ?? "—"}
      </td>
      <td className="px-4 py-2 text-right">
        <div className="flex gap-2 justify-end">
          <button
            onClick={onEdit}
            className="text-xs px-2 py-1 rounded bg-secondary hover:bg-secondary/80"
          >
            Edit
          </button>
          <button
            onClick={onDelete}
            className="text-xs px-2 py-1 rounded bg-destructive/20 text-red-400 hover:bg-destructive/40"
          >
            Delete
          </button>
        </div>
      </td>
    </tr>
  );
}

// ── New account modal ─────────────────────────────────────────────────────────

function NewAccountModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setLoading(true);
    try {
      await accountsApi.create({ name: name.trim(), description: desc.trim() || undefined });
      onCreated();
    } catch {
      toast.error("Failed to create account");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal title="New Account" onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium mb-1">Account name</label>
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. IBKR Cash, Savings"
            className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">Description (optional)</label>
          <input
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            placeholder="e.g. Interactive Brokers USD account"
            className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <div className="flex gap-2 justify-end pt-2">
          <button type="button" onClick={onClose} className="px-4 py-2 rounded-md bg-secondary text-sm">Cancel</button>
          <button type="submit" disabled={loading || !name.trim()} className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm disabled:opacity-50">
            {loading ? "Creating…" : "Create"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// ── Deposit / Withdraw modal ──────────────────────────────────────────────────

type TransactionModalProps =
  | { mode: "create"; type: "deposit" | "withdraw"; accountId: number; onClose: () => void; onDone: () => void }
  | { mode: "edit"; accountId: number; transaction: AccountTransaction; onClose: () => void; onDone: () => void };

function TransactionModal(props: TransactionModalProps) {
  const { mode, accountId, onClose, onDone } = props;
  const initial =
    mode === "edit"
      ? props.transaction
      : null;

  const [txType, setTxType] = useState<"deposit" | "withdrawal">(
    mode === "edit"
      ? initial!.transaction_type
      : props.type === "deposit" ? "deposit" : "withdrawal"
  );
  const [amount, setAmount] = useState(initial ? String(initial.amount) : "");
  const [currency, setCurrency] = useState(initial?.currency ?? "USD");
  const [notes, setNotes] = useState(initial?.notes ?? "");
  const [date, setDate] = useState(
    initial
      ? new Date(initial.transacted_at).toISOString().slice(0, 10)
      : new Date().toISOString().slice(0, 10)
  );
  const [loading, setLoading] = useState(false);
  const isDeposit = txType === "deposit";

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const amt = parseFloat(amount);
    if (!amt || amt <= 0) return;
    setLoading(true);
    try {
      if (mode === "edit") {
        await accountsApi.updateTransaction(accountId, initial!.id, {
          transaction_type: txType,
          amount: amt,
          currency,
          notes: notes.trim() ? notes.trim() : null,
          transacted_at: new Date(date).toISOString(),
        });
        toast.success("Transaction updated");
      } else {
        const payload = {
          amount: amt,
          currency,
          notes: notes.trim() || undefined,
          transacted_at: new Date(date).toISOString(),
        };
        if (isDeposit) {
          await accountsApi.deposit(accountId, payload);
        } else {
          await accountsApi.withdraw(accountId, payload);
        }
      }
      onDone();
    } catch (err) {
      const e = err as { response?: { data?: { detail?: string } } };
      toast.error(e?.response?.data?.detail ?? `Failed to save transaction`);
    } finally {
      setLoading(false);
    }
  };

  const title = mode === "edit" ? "Edit Transaction" : isDeposit ? "Deposit" : "Withdraw";

  return (
    <Modal title={title} onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {/* Type selector only in edit mode (create mode is locked by the
            button that opened the modal). */}
        {mode === "edit" && (
          <div>
            <label className="block text-sm font-medium mb-1">Type</label>
            <select
              value={txType}
              onChange={(e) => setTxType(e.target.value as "deposit" | "withdrawal")}
              className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <option value="deposit">Deposit</option>
              <option value="withdrawal">Withdrawal</option>
            </select>
          </div>
        )}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-sm font-medium mb-1">Amount</label>
            <input
              autoFocus
              type="number"
              min="0.01"
              step="0.01"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              placeholder="0.00"
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Currency</label>
            <select
              value={currency}
              onChange={(e) => setCurrency(e.target.value)}
              className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">Date</label>
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">Notes (optional)</label>
          <input
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="e.g. Monthly transfer"
            className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <div className="flex gap-2 justify-end pt-2">
          <button type="button" onClick={onClose} className="px-4 py-2 rounded-md bg-secondary text-sm">Cancel</button>
          <button
            type="submit"
            disabled={loading || !amount}
            className={`px-4 py-2 rounded-md text-sm text-white disabled:opacity-50 ${
              isDeposit ? "bg-emerald-600 hover:bg-emerald-500" : "bg-red-600 hover:bg-red-500"
            }`}
          >
            {loading ? "Saving…" : mode === "edit" ? "Save changes" : isDeposit ? "Deposit" : "Withdraw"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// ── Shared modal shell ────────────────────────────────────────────────────────

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-md rounded-xl border border-border bg-card p-6 shadow-xl">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground text-lg">✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}
