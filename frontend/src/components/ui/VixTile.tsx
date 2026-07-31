"use client";

/**
 * VIX at-a-glance tile for the dashboard.
 *
 * Shows current VIX level with tier colour (low_vol/high_vol/crisis
 * matching the regime classifier's thresholds) plus intraday change.
 * Polls every 5 minutes — VIX moves slowly enough that faster refresh
 * would just burn yfinance quota.
 */
import { useQuery } from "@tanstack/react-query";
import { marketApi } from "@/lib/api";

interface VixResponse {
  level: number;
  previous_close: number;
  change: number;
  change_pct: number;
  tier: "low_vol" | "high_vol" | "crisis";
}

const TIER_STYLES: Record<
  VixResponse["tier"],
  { label: string; text: string; ring: string; hint: string }
> = {
  low_vol: {
    label: "Calm",
    text: "text-emerald-500",
    ring: "ring-emerald-500/30",
    hint: "VIX < 20 — momentum longs favoured",
  },
  high_vol: {
    label: "Elevated",
    text: "text-amber-500",
    ring: "ring-amber-500/30",
    hint: "VIX 20–30 — choppy, tighten stops",
  },
  crisis: {
    label: "Crisis",
    text: "text-red-500",
    ring: "ring-red-500/40",
    hint: "VIX ≥ 30 — panic tier, often contrarian buy",
  },
};

export function VixTile() {
  const { data, isLoading } = useQuery<VixResponse>({
    queryKey: ["vix"],
    queryFn: () => marketApi.vix().then((r) => r.data),
    staleTime: 5 * 60_000,
    refetchInterval: 5 * 60_000,
    retry: 1,
  });

  if (isLoading || !data) {
    return (
      <div className="rounded-xl border border-border bg-card p-4 space-y-1">
        <p className="text-[10px] uppercase tracking-wider text-muted-foreground">VIX</p>
        <div className="h-8 flex items-center text-sm text-muted-foreground">Loading…</div>
      </div>
    );
  }

  const style = TIER_STYLES[data.tier];
  const changeSign = data.change >= 0 ? "+" : "";
  const changeColour = data.change >= 0 ? "text-red-400" : "text-emerald-400";

  return (
    <div className={`rounded-xl border border-border bg-card p-4 ring-1 ${style.ring}`}>
      <div className="flex items-baseline justify-between mb-1">
        <p className="text-[10px] uppercase tracking-wider text-muted-foreground">VIX</p>
        <span className={`text-[10px] uppercase font-semibold ${style.text}`}>
          {style.label}
        </span>
      </div>
      <div className="flex items-baseline gap-2">
        <span className={`text-2xl font-bold tabular-nums ${style.text}`}>
          {data.level.toFixed(2)}
        </span>
        <span className={`text-xs font-medium tabular-nums ${changeColour}`}>
          {changeSign}
          {data.change.toFixed(2)} ({changeSign}
          {data.change_pct.toFixed(2)}%)
        </span>
      </div>
      <p className="mt-1 text-[10px] text-muted-foreground leading-tight">{style.hint}</p>
    </div>
  );
}
