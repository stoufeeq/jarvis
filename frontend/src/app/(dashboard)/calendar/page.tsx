"use client";

import { useMemo, useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { calendarApi } from "@/lib/api";
import { TickerLink } from "@/components/ui/TickerLink";
import type { CalendarEvent } from "@/types";
import toast from "react-hot-toast";
import { RefreshCw } from "lucide-react";

const TYPE_LABEL: Record<string, string> = {
  earnings: "Earnings",
  ex_dividend: "Ex-Dividend",
  macro: "Macro",
};

const TYPE_PILL: Record<string, string> = {
  earnings: "bg-amber-500/10 text-amber-500 border-amber-500/30",
  ex_dividend: "bg-sky-500/10 text-sky-500 border-sky-500/30",
  macro: "bg-purple-500/10 text-purple-500 border-purple-500/30",
};

const TYPES = ["earnings", "ex_dividend", "macro"] as const;
type EventType = (typeof TYPES)[number];

export default function CalendarPage() {
  const [activeTypes, setActiveTypes] = useState<Set<EventType>>(new Set(TYPES));
  const [portfolioOnly, setPortfolioOnly] = useState(false);
  const [daysAhead, setDaysAhead] = useState(60);

  const { data: events = [], isLoading, refetch } = useQuery<CalendarEvent[]>({
    queryKey: ["calendar", daysAhead, portfolioOnly, [...activeTypes].sort().join(",")],
    queryFn: () =>
      calendarApi
        .upcoming({
          days_ahead: daysAhead,
          portfolio_only: portfolioOnly,
          types: [...activeTypes],
        })
        .then((r) => r.data),
    staleTime: 5 * 60_000,
  });

  const refresh = useMutation({
    mutationFn: () => calendarApi.refresh(),
    onSuccess: () => {
      toast.success("Calendar refresh dispatched — data will update shortly");
      setTimeout(() => refetch(), 30_000);
    },
    onError: () => toast.error("Failed to dispatch refresh"),
  });

  function toggleType(t: EventType) {
    setActiveTypes((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
  }

  // Group events by week-of for cleaner display
  const grouped = useMemo(() => groupByWeek(events), [events]);

  return (
    <div className="space-y-6 max-w-4xl">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold">Calendar</h1>
        <button
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-secondary text-sm font-medium hover:bg-secondary/80 disabled:opacity-50 transition-colors"
          title="Refresh from yfinance (background task, runs daily automatically)"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${refresh.isPending ? "animate-spin" : ""}`} />
          {refresh.isPending ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {/* Filters */}
      <div className="rounded-lg border border-border bg-card p-3 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground mr-1">Types:</span>
          {TYPES.map((t) => {
            const active = activeTypes.has(t);
            return (
              <button
                key={t}
                onClick={() => toggleType(t)}
                className={`px-2.5 py-1 rounded-full border text-xs font-medium transition-colors ${
                  active ? TYPE_PILL[t] : "border-border text-muted-foreground/50 line-through"
                }`}
              >
                {TYPE_LABEL[t]}
              </button>
            );
          })}
        </div>
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={portfolioOnly}
              onChange={(e) => setPortfolioOnly(e.target.checked)}
              className="accent-primary"
            />
            <span className="text-muted-foreground">Portfolio tickers only</span>
          </label>
          <span className="text-muted-foreground/40">·</span>
          <label className="flex items-center gap-2">
            <span className="text-muted-foreground">Window:</span>
            <select
              value={daysAhead}
              onChange={(e) => setDaysAhead(parseInt(e.target.value))}
              className="px-2 py-1 rounded border border-border bg-input text-xs"
            >
              <option value="14">Next 2 weeks</option>
              <option value="30">Next 30 days</option>
              <option value="60">Next 60 days</option>
              <option value="90">Next 90 days</option>
              <option value="180">Next 6 months</option>
            </select>
          </label>
        </div>
      </div>

      {/* Events */}
      {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
      {!isLoading && events.length === 0 && (
        <div className="rounded-xl border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
          No upcoming events match your filters.
          {events.length === 0 && (
            <p className="mt-2 text-xs">
              If your watchlist or portfolio has tickers, click <strong>Refresh</strong> to populate.
            </p>
          )}
        </div>
      )}

      {!isLoading && grouped.length > 0 && (
        <div className="space-y-6">
          {grouped.map(([weekLabel, weekEvents]) => (
            <div key={weekLabel} className="space-y-2">
              <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                {weekLabel}
              </h2>
              <div className="space-y-1.5">
                {weekEvents.map((e, i) => (
                  <EventRow key={`${e.ticker}-${e.type}-${e.date}-${i}`} event={e} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function EventRow({ event: e }: { event: CalendarEvent }) {
  const dateObj = new Date(e.date + "T00:00:00");
  const dateLabel = dateObj.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
  const pill = TYPE_PILL[e.type] ?? "border-border text-muted-foreground";

  return (
    <div className="flex items-center gap-3 p-3 rounded-lg border border-border bg-card hover:bg-secondary/30 transition-colors">
      <span className="text-xs text-muted-foreground shrink-0 w-24">{dateLabel}</span>
      <span className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full border shrink-0 ${pill}`}>
        {TYPE_LABEL[e.type]}
      </span>
      <div className="flex-1 min-w-0 flex items-center gap-2 flex-wrap">
        {e.ticker && <TickerLink ticker={e.ticker} className="font-semibold text-sm" />}
        {e.in_portfolio && (
          <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-500">
            held
          </span>
        )}
        <span className="text-sm text-muted-foreground truncate">{e.title}</span>
      </div>
      {e.details && (
        <span className="text-xs text-muted-foreground/60 truncate hidden sm:inline">{e.details}</span>
      )}
    </div>
  );
}

/** Group sorted-by-date events into "Today / This week / Next week / week of MMM dd / …" buckets. */
function groupByWeek(events: CalendarEvent[]): [string, CalendarEvent[]][] {
  if (events.length === 0) return [];
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const oneDay = 86400_000;

  const buckets = new Map<string, CalendarEvent[]>();
  for (const e of events) {
    const d = new Date(e.date + "T00:00:00");
    const diffDays = Math.floor((d.getTime() - today.getTime()) / oneDay);

    let label: string;
    if (diffDays === 0) label = "Today";
    else if (diffDays === 1) label = "Tomorrow";
    else if (diffDays < 7) label = "This week";
    else if (diffDays < 14) label = "Next week";
    else {
      // Group by week-of-Monday
      const monday = new Date(d);
      monday.setDate(d.getDate() - ((d.getDay() + 6) % 7));
      label = `Week of ${monday.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
    }
    if (!buckets.has(label)) buckets.set(label, []);
    buckets.get(label)!.push(e);
  }
  return [...buckets.entries()];
}
