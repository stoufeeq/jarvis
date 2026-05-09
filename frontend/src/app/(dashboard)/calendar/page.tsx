"use client";

import { useMemo, useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { calendarApi } from "@/lib/api";
import { TickerLink } from "@/components/ui/TickerLink";
import type { CalendarEvent } from "@/types";
import toast from "react-hot-toast";
import { RefreshCw, Download, ChevronLeft, ChevronRight } from "lucide-react";

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

const TYPE_DOT: Record<string, string> = {
  earnings: "bg-amber-500",
  ex_dividend: "bg-sky-500",
  macro: "bg-purple-500",
};

const TYPES = ["earnings", "ex_dividend", "macro"] as const;
type EventType = (typeof TYPES)[number];

type ViewMode = "list" | "grid";

export default function CalendarPage() {
  const [view, setView] = useState<ViewMode>("list");
  const [activeTypes, setActiveTypes] = useState<Set<EventType>>(new Set(TYPES));
  const [portfolioOnly, setPortfolioOnly] = useState(false);
  const [daysAhead, setDaysAhead] = useState(60);
  const [gridMonth, setGridMonth] = useState(() => {
    const d = new Date();
    return new Date(d.getFullYear(), d.getMonth(), 1);
  });

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

  function downloadIcs() {
    const accessToken =
      typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
    const url = calendarApi.exportIcsUrl({ days_ahead: 180, portfolio_only: portfolioOnly });

    // The .ics endpoint requires auth via Authorization header — fetch with
    // the JWT, then trigger a download from the resulting blob.
    fetch(url, {
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
    })
      .then(async (res) => {
        if (!res.ok) throw new Error("Export failed");
        const blob = await res.blob();
        const objUrl = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = objUrl;
        a.download = "jarvis-calendar.ics";
        a.click();
        URL.revokeObjectURL(objUrl);
        toast.success("Calendar downloaded");
      })
      .catch(() => toast.error("Failed to download calendar"));
  }

  const grouped = useMemo(() => groupByWeek(events), [events]);

  return (
    <div className="space-y-6 max-w-5xl">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold">Calendar</h1>
        <div className="flex items-center gap-2 flex-wrap">
          <ViewToggle view={view} onChange={setView} />
          <button
            onClick={downloadIcs}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-secondary text-sm font-medium hover:bg-secondary/80 transition-colors"
            title="Export as .ics for Google/Apple/Outlook calendar"
          >
            <Download className="w-3.5 h-3.5" />
            Export
          </button>
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
          {view === "list" && (
            <>
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
            </>
          )}
        </div>
      </div>

      {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}

      {!isLoading && events.length === 0 && (
        <div className="rounded-xl border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
          No upcoming events match your filters.
          <p className="mt-2 text-xs">
            If your watchlist or portfolio has tickers, click <strong>Refresh</strong> to populate.
          </p>
        </div>
      )}

      {!isLoading && events.length > 0 && view === "list" && (
        <ListView grouped={grouped} />
      )}

      {!isLoading && events.length > 0 && view === "grid" && (
        <GridView
          events={events}
          month={gridMonth}
          onPrevMonth={() => setGridMonth(addMonths(gridMonth, -1))}
          onNextMonth={() => setGridMonth(addMonths(gridMonth, 1))}
          onToday={() => {
            const d = new Date();
            setGridMonth(new Date(d.getFullYear(), d.getMonth(), 1));
          }}
        />
      )}
    </div>
  );
}

/* ── View toggle ──────────────────────────────────────────────────────── */

function ViewToggle({ view, onChange }: { view: ViewMode; onChange: (v: ViewMode) => void }) {
  return (
    <div className="flex gap-1 p-0.5 rounded-md bg-secondary/50 border border-border">
      {(["list", "grid"] as ViewMode[]).map((v) => (
        <button
          key={v}
          onClick={() => onChange(v)}
          className={`px-2.5 py-1 rounded text-xs font-medium transition-colors capitalize ${
            view === v
              ? "bg-background text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          {v}
        </button>
      ))}
    </div>
  );
}

/* ── List view ─────────────────────────────────────────────────────────── */

function ListView({ grouped }: { grouped: [string, CalendarEvent[]][] }) {
  return (
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
  const ivPill = ivPillFor(e);

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
        {ivPill && (
          <span className={`text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded ${ivPill.cls}`}
                title={ivPill.tooltip}>
            {ivPill.label}
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

/* ── Grid view ─────────────────────────────────────────────────────────── */

function GridView({
  events,
  month,
  onPrevMonth,
  onNextMonth,
  onToday,
}: {
  events: CalendarEvent[];
  month: Date;
  onPrevMonth: () => void;
  onNextMonth: () => void;
  onToday: () => void;
}) {
  const eventsByDate = useMemo(() => {
    const m = new Map<string, CalendarEvent[]>();
    for (const e of events) {
      if (!m.has(e.date)) m.set(e.date, []);
      m.get(e.date)!.push(e);
    }
    return m;
  }, [events]);

  const grid = useMemo(() => buildMonthGrid(month), [month]);
  const monthLabel = month.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const todayIso = isoDate(new Date());

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold">{monthLabel}</h2>
        <div className="flex items-center gap-1">
          <button
            onClick={onPrevMonth}
            className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
            title="Previous month"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
          <button
            onClick={onToday}
            className="px-2.5 py-1 rounded-md text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
          >
            Today
          </button>
          <button
            onClick={onNextMonth}
            className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
            title="Next month"
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Grid */}
      <div className="rounded-lg border border-border bg-card overflow-hidden">
        {/* Weekday header */}
        <div className="grid grid-cols-7 border-b border-border bg-secondary/30 text-[10px] uppercase tracking-wider text-muted-foreground">
          {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => (
            <div key={d} className="px-2 py-1.5 text-center">{d}</div>
          ))}
        </div>
        {/* Days */}
        <div className="grid grid-cols-7 auto-rows-fr">
          {grid.map((d, i) => {
            const iso = isoDate(d);
            const inMonth = d.getMonth() === month.getMonth();
            const isToday = iso === todayIso;
            const dayEvents = eventsByDate.get(iso) ?? [];
            return (
              <div
                key={i}
                className={`min-h-[80px] sm:min-h-[100px] p-1.5 border-r border-b border-border/50 last:border-r-0 ${
                  inMonth ? "" : "bg-secondary/10"
                } ${isToday ? "ring-1 ring-inset ring-primary/40 bg-primary/5" : ""}`}
              >
                <div className={`text-xs ${inMonth ? "text-foreground" : "text-muted-foreground/50"} ${isToday ? "font-bold text-primary" : ""}`}>
                  {d.getDate()}
                </div>
                <div className="mt-1 space-y-0.5">
                  {dayEvents.slice(0, 3).map((e, j) => (
                    <DayEventChip key={j} event={e} />
                  ))}
                  {dayEvents.length > 3 && (
                    <div className="text-[10px] text-muted-foreground px-1">
                      +{dayEvents.length - 3} more
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-3 flex-wrap text-xs text-muted-foreground">
        {TYPES.map((t) => (
          <div key={t} className="flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full ${TYPE_DOT[t]}`} />
            <span>{TYPE_LABEL[t]}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function DayEventChip({ event: e }: { event: CalendarEvent }) {
  const dot = TYPE_DOT[e.type] ?? "bg-muted-foreground";
  const ticker = e.ticker ?? "";
  const ivPill = ivPillFor(e);

  return (
    <div
      className="flex items-center gap-1 px-1 py-0.5 rounded text-[10px] hover:bg-secondary/40 transition-colors"
      title={`${e.title}${e.details ? " — " + e.details : ""}${ivPill ? " · " + ivPill.tooltip : ""}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dot}`} />
      <span className="truncate">
        {ticker ? (
          <TickerLink ticker={ticker} className="font-medium" stopPropagation />
        ) : (
          <span className="text-muted-foreground">{e.title.slice(0, 12)}</span>
        )}
      </span>
      {ivPill && <span className={`shrink-0 w-1 h-1 rounded-full ${ivPill.dotCls}`} />}
    </div>
  );
}

/* ── IV/HV pill helper ──────────────────────────────────────────────── */

function ivPillFor(e: CalendarEvent): { label: string; cls: string; dotCls: string; tooltip: string } | null {
  if (e.type !== "earnings" || e.iv_hv_ratio == null) return null;

  const ratio = e.iv_hv_ratio;
  let label: string;
  let cls: string;
  let dotCls: string;
  let tooltip = `IV/HV: ${ratio.toFixed(2)}`;

  if (ratio >= 1.5) {
    label = "IV CRUSH RISK";
    cls = "bg-red-500/10 text-red-500";
    dotCls = "bg-red-500";
    tooltip += " — IV is rich; long premium plays often lose despite correct direction";
  } else if (ratio >= 1.2) {
    label = "VOL RICH";
    cls = "bg-amber-500/10 text-amber-500";
    dotCls = "bg-amber-500";
    tooltip += " — options pricing in elevated vol";
  } else if (ratio <= 0.8) {
    label = "VOL CHEAP";
    cls = "bg-emerald-500/10 text-emerald-500";
    dotCls = "bg-emerald-500";
    tooltip += " — options at a relative discount";
  } else {
    return null; // normal range, don't clutter UI
  }

  if (e.implied_move_pct != null) {
    tooltip += ` · implied move ${e.implied_move_pct.toFixed(1)}%`;
  }

  return { label, cls, dotCls, tooltip };
}

/* ── Date helpers ──────────────────────────────────────────────────────── */

function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function addMonths(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth() + n, 1);
}

function buildMonthGrid(month: Date): Date[] {
  // 6 weeks (42 days) starting on the Monday on or before the 1st of the month
  const firstOfMonth = new Date(month.getFullYear(), month.getMonth(), 1);
  const weekdayIdx = (firstOfMonth.getDay() + 6) % 7;  // Mon=0..Sun=6
  const start = new Date(firstOfMonth);
  start.setDate(firstOfMonth.getDate() - weekdayIdx);

  const days: Date[] = [];
  for (let i = 0; i < 42; i++) {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    days.push(d);
  }
  return days;
}

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
      const monday = new Date(d);
      monday.setDate(d.getDate() - ((d.getDay() + 6) % 7));
      label = `Week of ${monday.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
    }
    if (!buckets.has(label)) buckets.set(label, []);
    buckets.get(label)!.push(e);
  }
  return [...buckets.entries()];
}
