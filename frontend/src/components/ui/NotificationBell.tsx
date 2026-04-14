"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { alertsApi } from "@/lib/api";
import type { Alert } from "@/types";
import toast from "react-hot-toast";

const ALERT_TYPE_LABEL: Record<string, string> = {
  price_above: "Price above",
  price_below: "Price below",
  signal: "Signal",
  pnl_threshold: "P&L threshold",
};

export function NotificationBell() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);

  const { data: alerts = [] } = useQuery<Alert[]>({
    queryKey: ["alerts"],
    queryFn: () => alertsApi.list().then((r) => r.data),
    refetchInterval: 60_000,
  });

  // Unread = triggered but not yet acknowledged
  const unread = alerts.filter((a) => a.is_triggered && !a.acknowledged_at);
  // Notification history = all triggered alerts (including acknowledged)
  const notifications = alerts
    .filter((a) => a.is_triggered)
    .sort((a, b) => new Date(b.triggered_at ?? b.created_at).getTime() - new Date(a.triggered_at ?? a.created_at).getTime());

  // Close on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    if (open) document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  const acknowledgeMutation = useMutation({
    mutationFn: (id: number) => alertsApi.acknowledge(id),
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

  function handleDismissAll() {
    unread.forEach((a) => acknowledgeMutation.mutate(a.id));
  }

  return (
    <div className="relative" ref={panelRef}>
      {/* Bell button */}
      <button
        onClick={() => setOpen((v) => !v)}
        className="relative p-2 rounded-md hover:bg-secondary transition-colors"
        aria-label="Notifications"
      >
        <BellIcon />
        {unread.length > 0 && (
          <span className="absolute top-1 right-1 w-4 h-4 rounded-full bg-red-500 text-white text-[10px] font-bold flex items-center justify-center leading-none">
            {unread.length > 9 ? "9+" : unread.length}
          </span>
        )}
      </button>

      {/* Dropdown panel */}
      {open && (
        <div className="absolute right-0 top-full mt-2 w-80 rounded-xl border border-border bg-card shadow-xl z-50 overflow-hidden">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-border">
            <span className="text-sm font-semibold">Notifications</span>
            {unread.length > 0 && (
              <button
                onClick={handleDismissAll}
                className="text-xs text-muted-foreground hover:text-foreground"
              >
                Dismiss all
              </button>
            )}
          </div>

          {/* List */}
          <div className="max-h-96 overflow-y-auto">
            {notifications.length === 0 ? (
              <p className="px-4 py-8 text-center text-sm text-muted-foreground">
                No notifications yet
              </p>
            ) : (
              notifications.map((a) => (
                <NotificationRow
                  key={a.id}
                  alert={a}
                  onDismiss={() => acknowledgeMutation.mutate(a.id)}
                  onRearm={() => rearmMutation.mutate(a.id)}
                  onDelete={() => deleteMutation.mutate(a.id)}
                />
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function NotificationRow({
  alert,
  onDismiss,
  onRearm,
  onDelete,
}: {
  alert: Alert;
  onDismiss: () => void;
  onRearm: () => void;
  onDelete: () => void;
}) {
  const isUnread = !alert.acknowledged_at;
  const label = ALERT_TYPE_LABEL[alert.alert_type] ?? alert.alert_type;
  const when = alert.triggered_at
    ? new Date(alert.triggered_at).toLocaleString()
    : "";

  return (
    <div
      className={`px-4 py-3 border-b border-border/50 last:border-0 ${
        isUnread ? "bg-amber-500/5" : ""
      }`}
    >
      <div className="flex items-start gap-2">
        {isUnread && (
          <span className="mt-1.5 w-2 h-2 rounded-full bg-amber-400 shrink-0" />
        )}
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold">
            {alert.ticker}
            <span className="ml-2 text-xs font-normal text-muted-foreground">
              {label} ${alert.threshold_value}
            </span>
          </p>
          {when && (
            <p className="text-xs text-muted-foreground mt-0.5">{when}</p>
          )}
          {/* Actions */}
          <div className="flex gap-3 mt-2">
            {isUnread && (
              <button
                onClick={onDismiss}
                className="text-xs text-muted-foreground hover:text-foreground"
              >
                Dismiss
              </button>
            )}
            <button
              onClick={onRearm}
              className="text-xs text-amber-400 hover:text-amber-300"
            >
              Re-arm
            </button>
            <button
              onClick={onDelete}
              className="text-xs text-red-400 hover:text-red-300"
            >
              Delete
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function BellIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
      <path d="M13.73 21a2 2 0 0 1-3.46 0" />
    </svg>
  );
}
