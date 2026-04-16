"use client";

import { useState, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { Treemap, ResponsiveContainer } from "recharts";
import { RefreshCw } from "lucide-react";
import { marketApi } from "@/lib/api";

// ── Types ────────────────────────────────────────────────────────────────────

interface HeatmapStock {
  ticker: string;
  name: string;
  weight: number;
  change_pct: number | null;
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

// Recharts tree node shape
interface TreeNode {
  name: string;
  size?: number;
  ticker?: string;
  company?: string;
  change_pct?: number | null;
  children?: TreeNode[];
}

// ── Color helpers ─────────────────────────────────────────────────────────────

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

// ── Custom treemap cell ───────────────────────────────────────────────────────

// Recharts injects all data fields + layout props into the content renderer
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function HeatmapCell(props: any) {
  const { x, y, width, height, depth, name, ticker, company, change_pct } = props;

  if (width < 2 || height < 2) return null;

  // Sector (branch) node — depth 1
  if (depth === 1) {
    return (
      <g>
        <rect
          x={x} y={y} width={width} height={height}
          fill="#0f172a" stroke="#334155" strokeWidth={2}
        />
        {width > 50 && height > 16 && (
          <text
            x={x + 6} y={y + 14}
            fill="#64748b" fontSize={10} fontWeight={600}
            style={{ userSelect: "none", pointerEvents: "none" }}
          >
            {name}
          </text>
        )}
      </g>
    );
  }

  // Stock (leaf) node — depth 2
  if (depth === 2) {
    const fill = getHeatColor(change_pct);
    const pctStr = formatPct(change_pct);
    const showTicker = width > 22 && height > 14;
    const showPct    = width > 32 && height > 28;
    const fontSize   = Math.max(7, Math.min(11, width / 5));
    const midX = x + width / 2;
    const midY = y + height / 2;

    return (
      <g>
        <title>{`${ticker}  ${company}\n${pctStr || "No data"}`}</title>
        <rect
          x={x + 1} y={y + 1} width={width - 2} height={height - 2}
          fill={fill} rx={2}
        />
        {showTicker && (
          <text
            x={midX}
            y={showPct ? midY - 5 : midY + fontSize * 0.4}
            textAnchor="middle"
            fill="rgba(255,255,255,0.92)"
            fontSize={fontSize}
            fontWeight={700}
            style={{ userSelect: "none", pointerEvents: "none" }}
          >
            {ticker}
          </text>
        )}
        {showPct && pctStr && (
          <text
            x={midX} y={midY + 8}
            textAnchor="middle"
            fill="rgba(255,255,255,0.65)"
            fontSize={Math.max(7, fontSize - 2)}
            style={{ userSelect: "none", pointerEvents: "none" }}
          >
            {pctStr}
          </text>
        )}
      </g>
    );
  }

  return null;
}

// ── Data transform ────────────────────────────────────────────────────────────

function toTreeData(response: HeatmapResponse): TreeNode[] {
  return response.sectors.map((sector) => ({
    name: sector.name,
    children: sector.children.map((stock) => ({
      name: stock.ticker,
      ticker: stock.ticker,
      company: stock.name,
      size: stock.weight,
      change_pct: stock.change_pct,
    })),
  }));
}

// ── Legend ────────────────────────────────────────────────────────────────────

const LEGEND = [
  { label: "≥+4%",   color: "#14532d" },
  { label: "+2%",    color: "#166534" },
  { label: "+0.5%",  color: "#15803d" },
  { label: "Flat",   color: "#1f2937" },
  { label: "−0.5%",  color: "#9f1239" },
  { label: "−2%",    color: "#b91c1c" },
  { label: "≤−4%",   color: "#7f1d1d" },
];

// ── Page ──────────────────────────────────────────────────────────────────────

export default function HeatmapPage() {
  const [fetchKey, setFetchKey] = useState(0);

  const { data, isFetching, dataUpdatedAt, error } = useQuery<HeatmapResponse>({
    queryKey: ["heatmap", fetchKey],
    queryFn: () => marketApi.heatmap().then((r) => r.data),
    staleTime: Infinity,   // never auto-refetch — manual refresh only
    retry: 1,
  });

  const refresh = useCallback(() => setFetchKey((k) => k + 1), []);

  const updatedAgo = dataUpdatedAt
    ? Math.round((Date.now() - dataUpdatedAt) / 1000)
    : null;

  const treeData = data ? toTreeData(data) : [];

  return (
    <div className="flex flex-col h-full space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-2xl font-bold">Market Heatmap</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            S&amp;P 500 — sized by index weight, colored by today&apos;s change
          </p>
        </div>
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

      {/* Legend */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-xs text-muted-foreground mr-1">Change:</span>
        {LEGEND.map((l) => (
          <div key={l.label} className="flex items-center gap-1">
            <div className="w-3 h-3 rounded-sm" style={{ background: l.color }} />
            <span className="text-xs text-muted-foreground">{l.label}</span>
          </div>
        ))}
      </div>

      {/* States */}
      {!data && isFetching && (
        <div className="flex-1 flex flex-col items-center justify-center gap-3 text-muted-foreground">
          <RefreshCw className="w-8 h-8 animate-spin opacity-40" />
          <div className="text-center">
            <p className="text-sm font-medium">Fetching ~200 quotes…</p>
            <p className="text-xs mt-1 opacity-60">First load takes 5–10 seconds</p>
          </div>
        </div>
      )}

      {!data && !isFetching && !error && (
        <div className="flex-1 flex flex-col items-center justify-center gap-3 text-muted-foreground">
          <p className="text-sm">Click <strong>Refresh</strong> to load the heatmap.</p>
        </div>
      )}

      {error && !data && (
        <div className="flex-1 flex items-center justify-center">
          <p className="text-sm text-red-400">Failed to load data. Try refreshing.</p>
        </div>
      )}

      {/* Treemap */}
      {data && treeData.length > 0 && (
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
      )}
    </div>
  );
}
