"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { alertsApi } from "@/lib/api";
import type { Alert } from "@/types";
import toast from "react-hot-toast";

const ALERT_TYPES = [
  { value: "price_above", label: "Price above" },
  { value: "price_below", label: "Price below" },
  { value: "signal", label: "Any signal fires" },
  { value: "pnl_threshold", label: "P&L threshold" },
];

export default function AlertsPage() {
  const qc = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    ticker: "",
    alert_type: "price_above",
    threshold_value: "",
    channels: "in_app",
  });

  const { data: alerts = [], isLoading } = useQuery<Alert[]>({
    queryKey: ["alerts"],
    queryFn: () => alertsApi.list().then((r) => r.data),
  });

  const createMutation = useMutation({
    mutationFn: () =>
      alertsApi.create({
        ticker: form.ticker.toUpperCase(),
        alert_type: form.alert_type,
        threshold_value: form.threshold_value ? parseFloat(form.threshold_value) : null,
        channels: form.channels,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alerts"] });
      setShowForm(false);
      setForm({ ticker: "", alert_type: "price_above", threshold_value: "", channels: "in_app" });
      toast.success("Alert created");
    },
    onError: () => toast.error("Failed to create alert"),
  });

  const toggleMutation = useMutation({
    mutationFn: ({ id, is_active }: { id: number; is_active: boolean }) =>
      alertsApi.update(id, { is_active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
  });

  const rearmMutation = useMutation({
    mutationFn: (id: number) => alertsApi.rearm(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alerts"] });
      toast.success("Alert re-armed");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => alertsApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alerts"] });
      toast.success("Alert deleted");
    },
  });

  const needsThreshold = form.alert_type !== "signal";

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Alerts</h1>
        <button
          onClick={() => setShowForm(true)}
          className="px-4 py-2 rounded-md bg-secondary text-sm font-medium hover:bg-secondary/80"
        >
          + New Alert
        </button>
      </div>

      {/* Create form */}
      {showForm && (
        <div className="rounded-xl border border-border bg-card p-5 space-y-4">
          <h3 className="font-semibold">New Alert</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Ticker</label>
              <input
                value={form.ticker}
                onChange={(e) => setForm({ ...form, ticker: e.target.value.toUpperCase() })}
                placeholder="e.g. AAPL"
                className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Type</label>
              <select
                value={form.alert_type}
                onChange={(e) => setForm({ ...form, alert_type: e.target.value })}
                className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm"
              >
                {ALERT_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            </div>
            {needsThreshold && (
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Threshold ($)</label>
                <input
                  type="number"
                  value={form.threshold_value}
                  onChange={(e) => setForm({ ...form, threshold_value: e.target.value })}
                  placeholder="e.g. 200.00"
                  className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
            )}
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Notify via</label>
              <select
                value={form.channels}
                onChange={(e) => setForm({ ...form, channels: e.target.value })}
                className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm"
              >
                <option value="in_app">In-app only</option>
                <option value="in_app,email">In-app + Email</option>
                <option value="in_app,telegram">In-app + Telegram</option>
                <option value="in_app,email,telegram">In-app + Email + Telegram</option>
              </select>
            </div>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => createMutation.mutate()}
              disabled={!form.ticker || createMutation.isPending}
              className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50"
            >
              {createMutation.isPending ? "Creating…" : "Create Alert"}
            </button>
            <button onClick={() => setShowForm(false)} className="text-sm text-muted-foreground">
              Cancel
            </button>
          </div>
        </div>
      )}

      {isLoading && <p className="text-muted-foreground text-sm">Loading…</p>}

      {!isLoading && alerts.length === 0 && (
        <div className="rounded-xl border border-dashed border-border p-10 text-center text-muted-foreground text-sm">
          No alerts set up yet.
        </div>
      )}

      <div className="space-y-2">
        {alerts.map((alert) => (
          <div
            key={alert.id}
            className={`flex items-center gap-4 rounded-xl border px-4 py-3 ${
              alert.is_triggered
                ? "border-emerald-500/40 bg-emerald-500/5"
                : alert.is_active
                ? "border-border bg-card"
                : "border-border/40 bg-card opacity-50"
            }`}
          >
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-semibold">{alert.ticker}</span>
                <span className="text-xs text-muted-foreground bg-secondary px-2 py-0.5 rounded-full">
                  {ALERT_TYPES.find((t) => t.value === alert.alert_type)?.label ?? alert.alert_type}
                </span>
                {alert.threshold_value && (
                  <span className="text-xs text-muted-foreground">${alert.threshold_value}</span>
                )}
                {alert.is_triggered && (
                  <span className="text-xs text-amber-400 font-medium">
                    ✓ Triggered {alert.triggered_at ? new Date(alert.triggered_at).toLocaleString() : ""}
                  </span>
                )}
              </div>
              <p className="text-xs text-muted-foreground mt-0.5">via {alert.channels}</p>
            </div>
            <div className="flex gap-2 shrink-0">
              {alert.is_triggered ? (
                <button
                  onClick={() => rearmMutation.mutate(alert.id)}
                  className="text-xs px-2 py-1 rounded bg-amber-500/20 text-amber-400 hover:bg-amber-500/30"
                >
                  Re-arm
                </button>
              ) : (
                <button
                  onClick={() => toggleMutation.mutate({ id: alert.id, is_active: !alert.is_active })}
                  className="text-xs px-2 py-1 rounded bg-secondary hover:bg-secondary/80"
                >
                  {alert.is_active ? "Pause" : "Resume"}
                </button>
              )}
              <button
                onClick={() => deleteMutation.mutate(alert.id)}
                className="text-xs px-2 py-1 rounded bg-destructive/20 text-red-400 hover:bg-destructive/40"
              >
                Delete
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
