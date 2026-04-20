"use client";

import { useEffect, useRef, useState, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { alertsApi, signalsApi, briefingApi } from "@/lib/api";
import type { Alert, Signal, Briefing } from "@/types";
import toast from "react-hot-toast";
import Link from "next/link";

// ── Unified notification type ───────────────────────────────────────────────

type NotificationType = "alert" | "signal" | "briefing";

interface Notification {
  id: string;
  type: NotificationType;
  title: string;
  subtitle: string;
  timestamp: Date;
  href?: string;
  // Alert-specific
  alertId?: number;
  isUnread?: boolean;
}

const ALERT_TYPE_LABEL: Record<string, string> = {
  price_above: "Price above",
  price_below: "Price below",
  signal: "Signal",
  pnl_threshold: "P&L threshold",
};

const SIGNAL_DIRECTION_ICON: Record<string, string> = {
  bullish: "▲",
  bearish: "▼",
  neutral: "●",
};

const TYPE_COLORS: Record<NotificationType, string> = {
  alert: "bg-amber-400",
  signal: "bg-blue-400",
  briefing: "bg-emerald-400",
};

// ── Component ───────────────────────────────────────────────────────────────

export function NotificationBell() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [dismissedSignals, setDismissedSignals] = useState<Set<string>>(() => {
    if (typeof window === "undefined") return new Set();
    const stored = localStorage.getItem("jarvis_dismissed_signals");
    return stored ? new Set(JSON.parse(stored)) : new Set();
  });
  const [dismissedBriefing, setDismissedBriefing] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return localStorage.getItem("jarvis_dismissed_briefing");
  });
  const panelRef = useRef<HTMLDivElement>(null);

  // ── Data queries ────────────────────────────────────────────────────────

  const { data: alerts = [] } = useQuery<Alert[]>({
    queryKey: ["alerts"],
    queryFn: () => alertsApi.list().then((r) => r.data),
    refetchInterval: 60_000,
  });

  const { data: signals = [] } = useQuery<Signal[]>({
    queryKey: ["signals", "notifications"],
    queryFn: () => signalsApi.list({ limit: 20 }).then((r) => r.data),
    staleTime: 5 * 60_000,
    refetchInterval: 5 * 60_000,
  });

  const { data: briefing } = useQuery<Briefing>({
    queryKey: ["briefing", "today"],
    queryFn: () => briefingApi.today().then((r) => r.data),
    staleTime: 5 * 60_000,
    retry: false,
  });

  // ── Build unified notifications ─────────────────────────────────────────

  const notifications = useMemo(() => {
    const items: Notification[] = [];

    // 1. Triggered alerts
    for (const a of alerts) {
      if (!a.is_triggered) continue;
      const label = ALERT_TYPE_LABEL[a.alert_type] ?? a.alert_type;
      items.push({
        id: `alert-${a.id}`,
        type: "alert",
        title: a.ticker,
        subtitle: `${label} $${a.threshold_value}`,
        timestamp: new Date(a.triggered_at ?? a.created_at),
        href: "/alerts",
        alertId: a.id,
        isUnread: !a.acknowledged_at,
      });
    }

    // 2. Strong signals (strength >= 4, last 24h)
    const oneDayAgo = Date.now() - 24 * 60 * 60 * 1000;
    for (const s of signals) {
      if (s.strength < 4) continue;
      const created = new Date(s.created_at).getTime();
      if (created < oneDayAgo) continue;
      const key = `signal-${s.id}`;
      const icon = SIGNAL_DIRECTION_ICON[s.direction] ?? "●";
      items.push({
        id: key,
        type: "signal",
        title: `${icon} ${s.ticker}`,
        subtitle: `${s.signal_type} ${s.direction} (${s.strength}/5)`,
        timestamp: new Date(s.created_at),
        href: "/signals",
        isUnread: !dismissedSignals.has(key),
      });
    }

    // 3. Daily briefing available
    if (briefing) {
      const briefingKey = `briefing-${briefing.briefing_date}`;
      items.push({
        id: briefingKey,
        type: "briefing",
        title: "Daily Briefing",
        subtitle: `${briefing.overall_sentiment} — ${briefing.briefing_date}`,
        timestamp: new Date(briefing.generated_at),
        href: "/briefing",
        isUnread: dismissedBriefing !== briefingKey,
      });
    }

    // Sort by timestamp descending
    items.sort((a, b) => b.timestamp.getTime() - a.timestamp.getTime());
    return items;
  }, [alerts, signals, briefing, dismissedSignals, dismissedBriefing]);

  const unreadCount = notifications.filter((n) => n.isUnread).length;

  // ── Persistence helpers ─────────────────────────────────────────────────

  function dismissSignal(id: string) {
    setDismissedSignals((prev) => {
      const next = new Set(prev);
      next.add(id);
      localStorage.setItem("jarvis_dismissed_signals", JSON.stringify([...next]));
      return next;
    });
  }

  function dismissBriefingNotif(id: string) {
    setDismissedBriefing(id);
    localStorage.setItem("jarvis_dismissed_briefing", id);
  }

  function dismissNotification(n: Notification) {
    if (n.type === "alert" && n.alertId) {
      acknowledgeMutation.mutate(n.alertId);
    } else if (n.type === "signal") {
      dismissSignal(n.id);
    } else if (n.type === "briefing") {
      dismissBriefingNotif(n.id);
    }
  }

  function handleDismissAll() {
    for (const n of notifications) {
      if (n.isUnread) dismissNotification(n);
    }
  }

  // ── Close on outside click ──────────────────────────────────────────────

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    if (open) document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  // ── Alert mutations ─────────────────────────────────────────────────────

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

  return (
    <div className="relative" ref={panelRef}>
      {/* Bell button */}
      <button
        onClick={() => setOpen((v) => !v)}
        className="relative p-2 rounded-md hover:bg-secondary transition-colors"
        aria-label="Notifications"
      >
        <BellIcon />
        {unreadCount > 0 && (
          <span className="absolute top-1 right-1 w-4 h-4 rounded-full bg-red-500 text-white text-[10px] font-bold flex items-center justify-center leading-none">
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>

      {/* Dropdown panel */}
      {open && (
        <div className="absolute right-0 top-full mt-2 w-80 rounded-xl border border-border bg-card shadow-xl z-50 overflow-hidden">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-border">
            <span className="text-sm font-semibold">Notifications</span>
            {unreadCount > 0 && (
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
              notifications.map((n) => (
                <NotificationRow
                  key={n.id}
                  notification={n}
                  onDismiss={() => dismissNotification(n)}
                  onRearm={n.type === "alert" && n.alertId ? () => rearmMutation.mutate(n.alertId!) : undefined}
                  onDelete={n.type === "alert" && n.alertId ? () => deleteMutation.mutate(n.alertId!) : undefined}
                  onClose={() => setOpen(false)}
                />
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Notification row ────────────────────────────────────────────────────────

function NotificationRow({
  notification: n,
  onDismiss,
  onRearm,
  onDelete,
  onClose,
}: {
  notification: Notification;
  onDismiss: () => void;
  onRearm?: () => void;
  onDelete?: () => void;
  onClose: () => void;
}) {
  const when = n.timestamp.toLocaleString();
  const dotColor = TYPE_COLORS[n.type];

  const content = (
    <div
      className={`px-4 py-3 border-b border-border/50 last:border-0 ${
        n.isUnread ? "bg-secondary/30" : ""
      }`}
    >
      <div className="flex items-start gap-2">
        <span className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${n.isUnread ? dotColor : "bg-transparent"}`} />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold">
            {n.title}
            <span className="ml-2 text-xs font-normal text-muted-foreground">
              {n.subtitle}
            </span>
          </p>
          <p className="text-xs text-muted-foreground mt-0.5">{when}</p>
          {/* Actions */}
          <div className="flex gap-3 mt-2">
            {n.isUnread && (
              <button
                onClick={(e) => { e.preventDefault(); e.stopPropagation(); onDismiss(); }}
                className="text-xs text-muted-foreground hover:text-foreground"
              >
                Dismiss
              </button>
            )}
            {onRearm && (
              <button
                onClick={(e) => { e.preventDefault(); e.stopPropagation(); onRearm(); }}
                className="text-xs text-amber-400 hover:text-amber-300"
              >
                Re-arm
              </button>
            )}
            {onDelete && (
              <button
                onClick={(e) => { e.preventDefault(); e.stopPropagation(); onDelete(); }}
                className="text-xs text-red-400 hover:text-red-300"
              >
                Delete
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );

  if (n.href) {
    return (
      <Link href={n.href} onClick={onClose} className="block hover:bg-secondary/20 transition-colors">
        {content}
      </Link>
    );
  }

  return content;
}

// ── Bell icon ───────────────────────────────────────────────────────────────

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
