"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { marketApi } from "@/lib/api";

type SessionState =
  | "open"
  | "pre_market"
  | "after_hours"
  | "closed_overnight"
  | "closed_weekend"
  | "closed_holiday";

interface MarketSession {
  state: SessionState;
  is_trading_day: boolean;
  is_weekend: boolean;
  is_holiday: boolean;
  current_et: string;
  next_open: string;
  todays_close: string | null;
  todays_open: string | null;
  description: string;
}

/**
 * Compact badge in the header showing US market session state plus a
 * countdown to the next state transition.
 *
 * Polls /market/session every 5 minutes, and updates the countdown text
 * locally every 30 seconds using Date arithmetic against the server's
 * ISO timestamps.
 *
 * Layouts:
 *   sm+:    [● OPEN · 1h 32m to close]
 *   xs:     [● 1h 32m]    (label hidden, dot + countdown)
 */
export function MarketSessionBadge() {
  const { data: session } = useQuery<MarketSession>({
    queryKey: ["market-session"],
    queryFn: () => marketApi.session().then((r) => r.data),
    staleTime: 5 * 60_000,
    refetchInterval: 5 * 60_000,
  });

  // Tick every 30s to update the countdown text without re-fetching
  const [, forceTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => forceTick((n) => n + 1), 30_000);
    return () => clearInterval(id);
  }, []);

  if (!session) {
    return (
      <div className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md bg-secondary/40 border border-border">
        <span className="w-2 h-2 rounded-full bg-muted-foreground/40 shrink-0" />
        <span className="text-xs text-muted-foreground/60 hidden sm:inline">…</span>
      </div>
    );
  }

  const { color, label, countdown, tooltip } = describeSession(session);
  const dotClass = {
    green: "bg-emerald-500 animate-pulse",
    amber: "bg-amber-500",
    red: "bg-red-500",
    grey: "bg-muted-foreground/60",
  }[color];

  return (
    <div
      className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md bg-secondary/40 border border-border whitespace-nowrap"
      title={tooltip}
    >
      <span className={`w-2 h-2 rounded-full shrink-0 ${dotClass}`} />
      <span className="text-xs font-medium text-foreground">{label}</span>
      <span className="text-xs text-muted-foreground/40">·</span>
      <span className="text-xs text-muted-foreground">{countdown}</span>
    </div>
  );
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function describeSession(s: MarketSession): {
  color: "green" | "amber" | "red" | "grey";
  label: string;
  countdown: string;
  tooltip: string;
} {
  const now = new Date();

  switch (s.state) {
    case "open": {
      const close = s.todays_close ? new Date(s.todays_close) : null;
      return {
        color: "green",
        label: "OPEN",
        countdown: close ? `${formatRelative(close, now)} to close` : "open",
        tooltip: s.description,
      };
    }
    case "pre_market": {
      const open = s.todays_open ? new Date(s.todays_open) : null;
      return {
        color: "amber",
        label: "PRE-MKT",
        countdown: open ? `opens in ${formatRelative(open, now)}` : "pre-market",
        tooltip: s.description,
      };
    }
    case "after_hours": {
      const next = new Date(s.next_open);
      return {
        color: "amber",
        label: "AFTER",
        countdown: `opens ${formatNextOpen(next, now)}`,
        tooltip: s.description,
      };
    }
    case "closed_overnight": {
      const next = new Date(s.next_open);
      return {
        color: "grey",
        label: "CLOSED",
        countdown: `opens ${formatNextOpen(next, now)}`,
        tooltip: s.description,
      };
    }
    case "closed_weekend": {
      const next = new Date(s.next_open);
      return {
        color: "grey",
        label: "WEEKEND",
        countdown: `opens ${formatNextOpen(next, now)}`,
        tooltip: s.description,
      };
    }
    case "closed_holiday": {
      const next = new Date(s.next_open);
      return {
        color: "grey",
        label: "HOLIDAY",
        countdown: `opens ${formatNextOpen(next, now)}`,
        tooltip: s.description,
      };
    }
  }
}

/** "1h 32m" or "47m" or "12s" — compact relative duration. */
function formatRelative(target: Date, now: Date): string {
  const diff = target.getTime() - now.getTime();
  if (diff <= 0) return "now";
  const totalSeconds = Math.floor(diff / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);

  if (hours >= 24) {
    const days = Math.floor(hours / 24);
    const remHours = hours % 24;
    return remHours > 0 ? `${days}d ${remHours}h` : `${days}d`;
  }
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m`;
  return "<1m";
}

/** "Mon 9:30am" if the next open is on a different calendar day, else "in Xh Ym". */
function formatNextOpen(target: Date, now: Date): string {
  const diffMs = target.getTime() - now.getTime();
  const diffHours = diffMs / (1000 * 60 * 60);

  // Same calendar day or within ~12 hours → relative
  const sameDay = target.toDateString() === now.toDateString();
  if (sameDay || diffHours < 12) {
    return `in ${formatRelative(target, now)}`;
  }

  // Multi-day → show day-of-week + time
  const day = target.toLocaleDateString(undefined, { weekday: "short" });
  const time = target.toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
  return `${day} ${time}`;
}
