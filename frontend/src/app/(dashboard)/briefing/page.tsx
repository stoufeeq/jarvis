"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { briefingApi } from "@/lib/api";
import type { Briefing, BriefingContent, BriefingPortfolioItem, BriefingWatchlistItem, BriefingSP500Item, BriefingMacroEvent } from "@/types";
import toast from "react-hot-toast";
import {
  RefreshCw,
  ChevronDown,
  ChevronUp,
  Trash2,
  TrendingUp,
  TrendingDown,
  Minus,
  Calendar,
  Briefcase,
  Eye,
  BarChart2,
  Globe,
  Clock,
} from "lucide-react";
import { format } from "date-fns";

// ── Helpers ───────────────────────────────────────────────────────────────────

const SENTIMENT_STYLE: Record<string, { bg: string; text: string; label: string }> = {
  bullish:  { bg: "bg-emerald-500/15", text: "text-emerald-400", label: "Bullish" },
  neutral:  { bg: "bg-slate-500/15",   text: "text-slate-400",   label: "Neutral" },
  cautious: { bg: "bg-amber-500/15",   text: "text-amber-400",   label: "Cautious" },
  bearish:  { bg: "bg-red-500/15",     text: "text-red-400",     label: "Bearish" },
};

const ACTION_STYLE: Record<string, string> = {
  hold:  "text-slate-400 bg-slate-500/15",
  trim:  "text-amber-400 bg-amber-500/15",
  add:   "text-emerald-400 bg-emerald-500/15",
  buy:   "text-emerald-400 bg-emerald-500/15",
  watch: "text-sky-400 bg-sky-500/15",
  exit:  "text-red-400 bg-red-500/15",
  avoid: "text-red-400 bg-red-500/15",
};

function SentimentIcon({ sentiment }: { sentiment: string }) {
  if (sentiment === "bullish") return <TrendingUp className="w-4 h-4 text-emerald-400" />;
  if (sentiment === "bearish") return <TrendingDown className="w-4 h-4 text-red-400" />;
  if (sentiment === "cautious") return <TrendingDown className="w-4 h-4 text-amber-400" />;
  return <Minus className="w-4 h-4 text-slate-400" />;
}

function ActionBadge({ action }: { action: string }) {
  const cls = ACTION_STYLE[action] ?? "text-slate-400 bg-slate-500/15";
  return (
    <span className={`text-xs font-semibold px-2 py-0.5 rounded-full uppercase tracking-wide ${cls}`}>
      {action}
    </span>
  );
}

function Section({
  title,
  icon,
  count,
  children,
  defaultOpen = true,
}: {
  title: string;
  icon: React.ReactNode;
  count?: number;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-lg border border-white/10 overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-3 bg-white/5 hover:bg-white/8 transition-colors"
      >
        <div className="flex items-center gap-2 text-sm font-semibold text-white">
          {icon}
          {title}
          {count !== undefined && (
            <span className="ml-1 text-xs text-slate-500 font-normal">({count})</span>
          )}
        </div>
        {open ? <ChevronUp className="w-4 h-4 text-slate-500" /> : <ChevronDown className="w-4 h-4 text-slate-500" />}
      </button>
      {open && <div className="divide-y divide-white/5">{children}</div>}
    </div>
  );
}

// ── Portfolio item card ───────────────────────────────────────────────────────

function PortfolioCard({ item }: { item: BriefingPortfolioItem }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="px-4 py-3 hover:bg-white/3 transition-colors">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-3 min-w-0">
          <span className="font-mono font-bold text-white text-sm">{item.ticker}</span>
          <ActionBadge action={item.action} />
          <span className="text-xs text-slate-400 truncate">{item.verdict}</span>
        </div>
        <button
          onClick={() => setExpanded(!expanded)}
          className="shrink-0 text-slate-600 hover:text-slate-400 transition-colors"
        >
          {expanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
        </button>
      </div>
      {expanded && (
        <p className="mt-2 text-xs text-slate-400 leading-relaxed">{item.reasoning}</p>
      )}
    </div>
  );
}

// ── Watchlist / SP500 item card ───────────────────────────────────────────────

function OpportunityCard({ item }: { item: BriefingWatchlistItem | BriefingSP500Item }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="px-4 py-3 hover:bg-white/3 transition-colors">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-3 min-w-0">
          <span className="font-mono font-bold text-white text-sm">{item.ticker}</span>
          <ActionBadge action={item.action} />
          <span className="text-xs text-slate-400 truncate">{item.verdict}</span>
        </div>
        <button
          onClick={() => setExpanded(!expanded)}
          className="shrink-0 text-slate-600 hover:text-slate-400 transition-colors"
        >
          {expanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
        </button>
      </div>
      {expanded && (
        <div className="mt-2 space-y-1.5">
          <p className="text-xs text-slate-400 leading-relaxed">{item.reasoning}</p>
          {item.catalyst && (
            <p className="text-xs text-sky-400">
              <span className="font-semibold">Catalyst:</span> {item.catalyst}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// ── Macro event row ───────────────────────────────────────────────────────────

function MacroEventRow({ event }: { event: BriefingMacroEvent }) {
  return (
    <div className="px-4 py-3">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-sm text-white font-medium">{event.event}</p>
          {event.impact && (
            <p className="text-xs text-slate-400 mt-0.5">{event.impact}</p>
          )}
        </div>
        {event.date && (
          <span className="shrink-0 text-xs text-slate-500 font-mono">{event.date}</span>
        )}
      </div>
    </div>
  );
}

// ── Full briefing view ────────────────────────────────────────────────────────

function BriefingView({ briefing }: { briefing: Briefing }) {
  const content = briefing.content as BriefingContent | null;
  const sentiment = briefing.overall_sentiment;
  const sentimentStyle = SENTIMENT_STYLE[sentiment] ?? SENTIMENT_STYLE.neutral;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-white">
            {format(new Date(briefing.briefing_date + "T00:00:00"), "EEEE, MMMM d, yyyy")}
          </h2>
          <div className="flex items-center gap-2 mt-1">
            <SentimentIcon sentiment={sentiment} />
            <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${sentimentStyle.bg} ${sentimentStyle.text}`}>
              {sentimentStyle.label}
            </span>
            <span className="text-xs text-slate-500">
              Generated {format(new Date(briefing.generated_at), "HH:mm")}
            </span>
          </div>
        </div>
      </div>

      {/* Market context */}
      {content?.market_context && (
        <div className="rounded-lg border border-white/10 bg-white/3 px-4 py-3">
          <p className="text-sm text-slate-300 leading-relaxed">{content.market_context}</p>
        </div>
      )}

      {/* Summary bullets */}
      {content?.summary_bullets && content.summary_bullets.length > 0 && (
        <div className="rounded-lg border border-white/10 bg-white/3 px-4 py-3">
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Key Takeaways</p>
          <ul className="space-y-1.5">
            {content.summary_bullets.map((b, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-slate-300">
                <span className="mt-1.5 w-1 h-1 rounded-full bg-indigo-400 shrink-0" />
                {b}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Portfolio */}
      {content?.portfolio && content.portfolio.length > 0 && (
        <Section
          title="Portfolio"
          icon={<Briefcase className="w-4 h-4 text-indigo-400" />}
          count={content.portfolio.length}
        >
          {content.portfolio.map((item) => (
            <PortfolioCard key={item.ticker} item={item} />
          ))}
        </Section>
      )}

      {/* Watchlist opportunities */}
      {content?.watchlist_opportunities && content.watchlist_opportunities.length > 0 && (
        <Section
          title="Watchlist Opportunities"
          icon={<Eye className="w-4 h-4 text-sky-400" />}
          count={content.watchlist_opportunities.length}
        >
          {content.watchlist_opportunities.map((item) => (
            <OpportunityCard key={item.ticker} item={item} />
          ))}
        </Section>
      )}

      {/* S&P 500 opportunities */}
      {content?.sp500_opportunities && content.sp500_opportunities.length > 0 && (
        <Section
          title="S&P 500 Opportunities"
          icon={<BarChart2 className="w-4 h-4 text-emerald-400" />}
          count={content.sp500_opportunities.length}
        >
          {content.sp500_opportunities.map((item) => (
            <OpportunityCard key={item.ticker} item={item} />
          ))}
        </Section>
      )}

      {/* Macro events */}
      {content?.macro_events && content.macro_events.length > 0 && (
        <Section
          title="Macro Events"
          icon={<Globe className="w-4 h-4 text-amber-400" />}
          count={content.macro_events.length}
          defaultOpen={false}
        >
          {content.macro_events.map((ev, i) => (
            <MacroEventRow key={i} event={ev} />
          ))}
        </Section>
      )}
    </div>
  );
}

// ── History sidebar item ──────────────────────────────────────────────────────

function HistoryItem({
  briefing,
  active,
  onClick,
  onDelete,
}: {
  briefing: Briefing;
  active: boolean;
  onClick: () => void;
  onDelete: () => void;
}) {
  const sentiment = briefing.overall_sentiment;
  const style = SENTIMENT_STYLE[sentiment] ?? SENTIMENT_STYLE.neutral;

  return (
    <div
      className={`group flex items-center justify-between px-3 py-2.5 rounded-lg cursor-pointer transition-colors ${
        active ? "bg-white/10" : "hover:bg-white/5"
      }`}
      onClick={onClick}
    >
      <div className="min-w-0">
        <p className="text-xs font-medium text-white truncate">
          {format(new Date(briefing.briefing_date + "T00:00:00"), "MMM d, yyyy")}
        </p>
        <span className={`text-[10px] font-semibold ${style.text}`}>{style.label}</span>
      </div>
      <button
        onClick={(e) => { e.stopPropagation(); onDelete(); }}
        className="opacity-0 group-hover:opacity-100 text-slate-600 hover:text-red-400 transition-colors ml-2"
      >
        <Trash2 className="w-3 h-3" />
      </button>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function BriefingPage() {
  const qc = useQueryClient();
  const [activeBriefingId, setActiveBriefingId] = useState<number | null>(null);
  const [showMobileHistory, setShowMobileHistory] = useState(false);

  // Today's briefing
  const {
    data: today,
    isLoading: loadingToday,
    error: todayError,
  } = useQuery<Briefing>({
    queryKey: ["briefing", "today"],
    queryFn: () => briefingApi.today().then((r) => r.data),
    staleTime: 1000 * 60 * 5,
  });

  // History list
  const { data: history = [] } = useQuery<Briefing[]>({
    queryKey: ["briefing", "history"],
    queryFn: () => briefingApi.history().then((r) => r.data),
  });

  // Specific briefing by id (for history navigation)
  const { data: selectedBriefing, isLoading: loadingSelected } = useQuery<Briefing>({
    queryKey: ["briefing", activeBriefingId],
    queryFn: () => briefingApi.get(activeBriefingId!).then((r) => r.data),
    enabled: !!activeBriefingId,
  });

  const regenerateMutation = useMutation({
    mutationFn: () => briefingApi.regenerate(),
    onSuccess: (res) => {
      qc.setQueryData(["briefing", "today"], res.data);
      qc.invalidateQueries({ queryKey: ["briefing", "history"] });
      setActiveBriefingId(null);
      toast.success("Briefing regenerated");
    },
    onError: () => toast.error("Failed to regenerate briefing"),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => briefingApi.delete(id),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: ["briefing", "history"] });
      if (activeBriefingId === id) setActiveBriefingId(null);
      if (today?.id === id) qc.invalidateQueries({ queryKey: ["briefing", "today"] });
      toast.success("Briefing deleted");
    },
    onError: () => toast.error("Failed to delete briefing"),
  });

  const displayBriefing = activeBriefingId ? selectedBriefing : today;
  const isLoading = activeBriefingId ? loadingSelected : loadingToday;

  return (
    <div className="md:flex md:h-[calc(100dvh-56px)] md:overflow-hidden">
      {/* ── Desktop sidebar ─────────────────────────────────── */}
      <aside className="hidden md:flex flex-col w-52 shrink-0 border-r border-white/10 bg-[#0f1117]">
        <div className="flex items-center justify-between px-4 py-3 border-b border-white/10">
          <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">History</span>
        </div>
        <div className="flex-1 overflow-y-auto py-2 px-2 space-y-0.5">
          {/* Today entry */}
          {today && (
            <HistoryItem
              briefing={today}
              active={activeBriefingId === null}
              onClick={() => setActiveBriefingId(null)}
              onDelete={() => deleteMutation.mutate(today.id)}
            />
          )}
          {/* Past briefings (exclude today) */}
          {history
            .filter((b) => b.id !== today?.id)
            .map((b) => (
              <HistoryItem
                key={b.id}
                briefing={b}
                active={activeBriefingId === b.id}
                onClick={() => setActiveBriefingId(b.id)}
                onDelete={() => deleteMutation.mutate(b.id)}
              />
            ))}
          {history.length === 0 && !today && (
            <p className="text-xs text-slate-600 px-3 py-4 text-center">No briefings yet</p>
          )}
        </div>
      </aside>

      {/* ── Main content ─────────────────────────────────────── */}
      <main className="md:flex-1 md:flex md:flex-col md:overflow-hidden">
        {/* Toolbar */}
        <div className="flex items-center justify-between px-4 md:px-6 py-3 border-b border-white/10 shrink-0">
          <div className="flex items-center gap-2">
            {/* Mobile history toggle */}
            <button
              className="md:hidden p-1.5 rounded text-slate-400 hover:text-white hover:bg-white/8 transition-colors"
              onClick={() => setShowMobileHistory(!showMobileHistory)}
            >
              <Clock className="w-4 h-4" />
            </button>
            <h1 className="text-base font-bold text-white flex items-center gap-2">
              <Calendar className="w-4 h-4 text-indigo-400" />
              Daily Briefing
            </h1>
          </div>
          <button
            onClick={() => regenerateMutation.mutate()}
            disabled={regenerateMutation.isPending}
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white font-medium transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${regenerateMutation.isPending ? "animate-spin" : ""}`} />
            {regenerateMutation.isPending ? "Generating…" : "Regenerate"}
          </button>
        </div>

        {/* Mobile history drawer */}
        {showMobileHistory && (
          <div className="md:hidden border-b border-white/10 bg-[#0f1117] px-2 py-2 space-y-0.5 max-h-48 overflow-y-auto">
            {today && (
              <HistoryItem
                briefing={today}
                active={activeBriefingId === null}
                onClick={() => { setActiveBriefingId(null); setShowMobileHistory(false); }}
                onDelete={() => deleteMutation.mutate(today.id)}
              />
            )}
            {history.filter((b) => b.id !== today?.id).map((b) => (
              <HistoryItem
                key={b.id}
                briefing={b}
                active={activeBriefingId === b.id}
                onClick={() => { setActiveBriefingId(b.id); setShowMobileHistory(false); }}
                onDelete={() => deleteMutation.mutate(b.id)}
              />
            ))}
          </div>
        )}

        {/* Content */}
        <div className="md:flex-1 md:overflow-y-auto px-4 md:px-6 py-4">
          {isLoading ? (
            <div className="flex flex-col items-center justify-center h-64 gap-3">
              <RefreshCw className="w-6 h-6 text-indigo-400 animate-spin" />
              <p className="text-sm text-slate-500">Generating your briefing…</p>
              <p className="text-xs text-slate-600">This may take up to 30 seconds on first load</p>
            </div>
          ) : todayError && !activeBriefingId ? (
            <div className="flex flex-col items-center justify-center h-64 gap-3">
              <p className="text-sm text-red-400">Failed to load briefing</p>
              <button
                onClick={() => qc.invalidateQueries({ queryKey: ["briefing", "today"] })}
                className="text-xs text-slate-400 hover:text-white underline"
              >
                Retry
              </button>
            </div>
          ) : displayBriefing ? (
            <BriefingView briefing={displayBriefing} />
          ) : (
            <div className="flex flex-col items-center justify-center h-64 gap-3">
              <p className="text-sm text-slate-500">No briefing selected</p>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
