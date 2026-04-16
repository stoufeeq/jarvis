"use client";

import { useState, useCallback } from "react";
import {
  Treemap,
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  ZAxis,
  CartesianGrid,
  ReferenceLine,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { RefreshCw } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { marketApi } from "@/lib/api";
import { cn } from "@/lib/utils";

// ── Types ────────────────────────────────────────────────────────────────────

interface HeatmapStock {
  ticker: string;
  name: string;
  weight: number;
  change_pct: number | null;
  rel_volume: number | null;
}

interface HeatmapSector {
  name: string;
  children: HeatmapStock[];
}

interface HeatmapResponse {
  sectors: HeatmapSector[];
  cached_at: number | null;
  error?: string;
}

interface TreeNode {
  name: string;
  size?: number;
  ticker?: string;
  company?: string;
  change_pct?: number | null;
  children?: TreeNode[];
}

interface BubblePoint {
  x: number;
  y: number;
  z: number;
  ticker: string;
  company: string;
  sector: string;
}

// ── Constants ─────────────────────────────────────────────────────────────────

const SECTOR_COLORS: Record<string, string> = {
  "Information Technology": "#3b82f6",
  "Health Care":            "#10b981",
  "Financials":             "#f59e0b",
  "Consumer Discretionary": "#a855f7",
  "Communication Services": "#06b6d4",
  "Industrials":            "#f97316",
  "Consumer Staples":       "#84cc16",
  "Energy":                 "#ef4444",
  "Utilities":              "#6366f1",
  "Real Estate":            "#ec4899",
  "Materials":              "#14b8a6",
};

const HEATMAP_LEGEND = [
  { label: "≥+4%",  color: "#14532d" },
  { label: "+2%",   color: "#166534" },
  { label: "+0.5%", color: "#15803d" },
  { label: "Flat",  color: "#1f2937" },
  { label: "−0.5%", color: "#9f1239" },
  { label: "−2%",   color: "#b91c1c" },
  { label: "≤−4%",  color: "#7f1d1d" },
];

// ── Heatmap helpers ───────────────────────────────────────────────────────────

function getHeatColor(pct: number | null | undefined): string {
  if (pct == null) return "#1f2937";
  if (pct >= 4)    return "#14532d";
  if (pct >= 2)    return "#166534";
  if (pct >= 0.5)  return "#15803d";
  if (pct >= 0.1)  return "#16a34a";
  if (pct > -0.1)  return "#1f2937";
  if (pct > -0.5)  return "#9f1239";
  if (pct > -2)    return "#b91c1c";
  if (pct > -4)    return "#991b1b";
  return "#7f1d1d";
}

function formatPct(pct: number | null | undefined): string {
  if (pct == null) return "";
  return (pct >= 0 ? "+" : "") + pct.toFixed(2) + "%";
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function HeatmapCell(props: any) {
  const { x, y, width, height, depth, name, ticker, company, change_pct } = props;
  if (width < 2 || height < 2) return null;

  if (depth === 1) {
    return (
      <g>
        <rect x={x} y={y} width={width} height={height}
          fill="#0f172a" stroke="#334155" strokeWidth={2} />
        {width > 50 && height > 16 && (
          <text x={x + 6} y={y + 14} fill="#64748b" fontSize={10} fontWeight={600}
            style={{ userSelect: "none", pointerEvents: "none" }}>
            {name}
          </text>
        )}
      </g>
    );
  }

  if (depth === 2) {
    const fill     = getHeatColor(change_pct);
    const pctStr   = formatPct(change_pct);
    const showLabel = width > 22 && height > 14;
    const showPct   = width > 32 && height > 28;
    const fontSize  = Math.max(7, Math.min(11, width / 5));
    const midX = x + width / 2;
    const midY = y + height / 2;

    return (
      <g>
        <title>{`${ticker}  ${company}\n${pctStr || "No data"}`}</title>
        <rect x={x + 1} y={y + 1} width={width - 2} height={height - 2} fill={fill} rx={2} />
        {showLabel && (
          <text x={midX} y={showPct ? midY - 5 : midY + fontSize * 0.4}
            textAnchor="middle" fill="rgba(255,255,255,0.92)"
            fontSize={fontSize} fontWeight={700}
            style={{ userSelect: "none", pointerEvents: "none" }}>
            {ticker}
          </text>
        )}
        {showPct && pctStr && (
          <text x={midX} y={midY + 8} textAnchor="middle"
            fill="rgba(255,255,255,0.65)" fontSize={Math.max(7, fontSize - 2)}
            style={{ userSelect: "none", pointerEvents: "none" }}>
            {pctStr}
          </text>
        )}
      </g>
    );
  }

  return null;
}

function toTreeData(response: HeatmapResponse): TreeNode[] {
  return response.sectors.map((sector) => ({
    name: sector.name,
    children: sector.children.map((s) => ({
      name:       s.ticker,
      ticker:     s.ticker,
      company:    s.name,
      size:       s.weight,
      change_pct: s.change_pct,
    })),
  }));
}

// ── Bubbles helpers ───────────────────────────────────────────────────────────

function toBubblesBySector(response: HeatmapResponse): Record<string, BubblePoint[]> {
  const out: Record<string, BubblePoint[]> = {};
  for (const sector of response.sectors) {
    const points: BubblePoint[] = [];
    for (const s of sector.children) {
      if (s.change_pct == null || s.rel_volume == null) continue;
      // Cap relative volume at 6× to keep the Y axis readable
      const y = Math.min(s.rel_volume, 6);
      points.push({
        x:       s.change_pct,
        y,
        z:       s.weight,
        ticker:  s.ticker,
        company: s.name,
        sector:  sector.name,
      });
    }
    if (points.length) out[sector.name] = points;
  }
  return out;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function BubbleTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const d: BubblePoint = payload[0].payload;
  return (
    <div className="bg-card border border-border rounded-lg px-3 py-2 text-xs shadow-xl">
      <p className="font-bold text-sm mb-1">{d.ticker} <span className="text-muted-foreground font-normal">— {d.company}</span></p>
      <p className="text-muted-foreground">{d.sector}</p>
      <p className={d.x >= 0 ? "text-emerald-400" : "text-red-400"}>
        Day change: {formatPct(d.x)}
      </p>
      <p className="text-muted-foreground">
        Rel. volume: {d.y.toFixed(2)}×
      </p>
    </div>
  );
}

// ── Shared shell ──────────────────────────────────────────────────────────────

function LoadingState() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-3 text-muted-foreground">
      <RefreshCw className="w-8 h-8 animate-spin opacity-40" />
      <div className="text-center">
        <p className="text-sm font-medium">Fetching ~450 quotes…</p>
        <p className="text-xs mt-1 opacity-60">First load takes 5–10 seconds</p>
      </div>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

type Tab = "heatmap" | "bubbles";

export default function HeatmapPage() {
  const [fetchKey, setFetchKey]           = useState(0);
  const [tab, setTab]                     = useState<Tab>("heatmap");
  const [activeSectors, setActiveSectors] = useState<Set<string>>(
    () => new Set(Object.keys(SECTOR_COLORS))
  );

  const toggleSector = useCallback((sector: string) => {
    setActiveSectors((prev) => {
      const next = new Set(prev);
      if (next.has(sector)) next.delete(sector); else next.add(sector);
      return next;
    });
  }, []);

  const allActive  = activeSectors.size === Object.keys(SECTOR_COLORS).length;
  const noneActive = activeSectors.size === 0;

  const { data, isFetching, dataUpdatedAt, error } = useQuery<HeatmapResponse>({
    queryKey: ["heatmap", fetchKey],
    queryFn:  () => marketApi.heatmap().then((r) => r.data),
    staleTime: Infinity,
    retry: 1,
  });

  const refresh = useCallback(() => setFetchKey((k) => k + 1), []);

  const updatedAgo = dataUpdatedAt
    ? Math.round((Date.now() - dataUpdatedAt) / 1000)
    : null;

  const treeData      = data ? toTreeData(data) : [];
  const bubblesBySector = data ? toBubblesBySector(data) : {};

  return (
    <div className="flex flex-col h-full space-y-3">

      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h1 className="text-2xl font-bold">Market Map</h1>
        <div className="flex items-center gap-3">
          {updatedAgo !== null && !isFetching && (
            <span className="text-xs text-muted-foreground">
              Updated {updatedAgo < 60 ? `${updatedAgo}s` : `${Math.floor(updatedAgo / 60)}m`} ago
            </span>
          )}
          <button
            onClick={refresh}
            disabled={isFetching}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-secondary text-sm font-medium hover:bg-secondary/80 disabled:opacity-50 transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isFetching ? "animate-spin" : ""}`} />
            {isFetching ? "Loading…" : "Refresh"}
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 p-1 rounded-lg bg-secondary/40 border border-border w-fit">
        {(["heatmap", "bubbles"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              "px-4 py-1.5 rounded-md text-sm font-medium transition-colors capitalize",
              tab === t
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Empty / loading states */}
      {!data && isFetching && <LoadingState />}

      {!data && !isFetching && !error && (
        <div className="flex-1 flex items-center justify-center text-muted-foreground">
          <p className="text-sm">Click <strong>Refresh</strong> to load data.</p>
        </div>
      )}

      {error && !data && (
        <div className="flex-1 flex items-center justify-center">
          <p className="text-sm text-red-400">Failed to load data. Try refreshing.</p>
        </div>
      )}

      {/* ── Heatmap tab ──────────────────────────────────────────────────────── */}
      {data && tab === "heatmap" && (
        <>
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="text-xs text-muted-foreground mr-1">Change:</span>
            {HEATMAP_LEGEND.map((l) => (
              <div key={l.label} className="flex items-center gap-1">
                <div className="w-3 h-3 rounded-sm" style={{ background: l.color }} />
                <span className="text-xs text-muted-foreground">{l.label}</span>
              </div>
            ))}
          </div>
          <div className="flex-1 min-h-0 rounded-xl border border-border overflow-hidden bg-[#0f172a]">
            <ResponsiveContainer width="100%" height="100%">
              <Treemap
                data={treeData}
                dataKey="size"
                aspectRatio={4 / 3}
                content={<HeatmapCell />}
                isAnimationActive={false}
              />
            </ResponsiveContainer>
          </div>
        </>
      )}

      {/* ── Bubbles tab ───────────────────────────────────────────────────────── */}
      {data && tab === "bubbles" && (
        <>
          {/* Sector legend — click to filter */}
          <div className="flex items-center gap-2 flex-wrap">
            {Object.entries(SECTOR_COLORS).map(([sector, color]) => {
              const active = activeSectors.has(sector);
              return (
                <button
                  key={sector}
                  onClick={() => toggleSector(sector)}
                  className={cn(
                    "flex items-center gap-1 px-2 py-0.5 rounded-full border text-xs transition-all",
                    active
                      ? "border-transparent text-foreground"
                      : "border-border text-muted-foreground/40 line-through"
                  )}
                  style={{ background: active ? color + "22" : "transparent" }}
                  title={active ? `Hide ${sector}` : `Show ${sector}`}
                >
                  <div
                    className="w-2 h-2 rounded-full shrink-0 transition-opacity"
                    style={{ background: color, opacity: active ? 1 : 0.3 }}
                  />
                  {sector}
                </button>
              );
            })}
            <div className="flex gap-1 ml-1">
              <button
                onClick={() => setActiveSectors(new Set(Object.keys(SECTOR_COLORS)))}
                disabled={allActive}
                className="text-xs text-muted-foreground hover:text-foreground disabled:opacity-30 px-1"
              >
                All
              </button>
              <span className="text-muted-foreground/30 text-xs">|</span>
              <button
                onClick={() => setActiveSectors(new Set())}
                disabled={noneActive}
                className="text-xs text-muted-foreground hover:text-foreground disabled:opacity-30 px-1"
              >
                None
              </button>
            </div>
          </div>

          <div className="flex-1 min-h-0 rounded-xl border border-border bg-card p-4">
            <div className="text-xs text-muted-foreground mb-2 flex gap-4">
              <span>X: Day change %</span>
              <span>Y: Relative volume (today ÷ 20-day avg)</span>
              <span>Size: Index weight</span>
            </div>
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 10, right: 20, bottom: 30, left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis
                  type="number"
                  dataKey="x"
                  name="Change"
                  unit="%"
                  tick={{ fontSize: 11, fill: "#64748b" }}
                  label={{ value: "Day Change (%)", position: "insideBottom", offset: -15, fontSize: 11, fill: "#64748b" }}
                />
                <YAxis
                  type="number"
                  dataKey="y"
                  name="Rel. Volume"
                  tick={{ fontSize: 11, fill: "#64748b" }}
                  label={{ value: "Rel. Volume", angle: -90, position: "insideLeft", offset: 10, fontSize: 11, fill: "#64748b" }}
                />
                <ZAxis type="number" dataKey="z" range={[40, 1800]} />
                <Tooltip content={<BubbleTooltip />} cursor={{ strokeDasharray: "3 3" }} />
                {/* Reference lines */}
                <ReferenceLine x={0} stroke="#475569" strokeDasharray="4 2" />
                <ReferenceLine y={1} stroke="#475569" strokeDasharray="4 2" label={{ value: "avg vol", position: "right", fontSize: 10, fill: "#475569" }} />
                {/* One Scatter per sector — filtered by activeSectors */}
                {Object.entries(bubblesBySector).filter(([sector]) => activeSectors.has(sector)).map(([sector, points]) => (
                  <Scatter
                    key={sector}
                    name={sector}
                    data={points}
                    fill={SECTOR_COLORS[sector] ?? "#94a3b8"}
                    fillOpacity={0.75}
                    isAnimationActive={false}
                  />
                ))}
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </div>
  );
}
