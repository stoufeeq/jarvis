"use client";

/**
 * Portfolio Allocation tab — how holdings distribute across sectors,
 * asset types and single names.
 *
 * Form choice: horizontal bars, not a pie. The reader's job here is
 * "compare magnitude, low → high" across categories with long names
 * (Information Technology, Consumer Discretionary), which a pie does
 * badly — angle is hard to compare and labels don't fit. Sector share is
 * also a magnitude, not an identity, so colour is a single-hue
 * SEQUENTIAL ramp rather than one hue per sector: eleven categorical
 * hues would be indistinguishable under colour-vision deficiency and
 * would imply the sectors are the subject, when the ranking is.
 *
 * The ramp is one hue at descending opacity so it holds up across all
 * eight themes — bigger is always more saturated, whether the surface
 * behind it is near-black or near-white. Values are direct-labelled, so
 * nothing depends on reading colour.
 *
 * Over/underweight vs the S&P 500 is deliberately NOT coloured
 * red/green. Being overweight a sector is a fact, not a loss, and the
 * app already spends green/red on P&L.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { portfolioApi } from "@/lib/api";
import { formatCurrency } from "@/lib/utils";

interface Group {
  name: string;
  value: number;
  pct: number;
  count: number;
  tickers: string[];
  benchmark_pct?: number | null;
  vs_benchmark_pp?: number | null;
}

interface Holding {
  ticker: string;
  name: string | null;
  sector: string;
  value: number;
  pct: number;
  portfolios: string[];
}

interface AllocationResponse {
  base_currency: string;
  total_value: number;
  portfolio_names: string[];
  holdings_count: number;
  by_sector: Group[];
  by_asset_type: Group[];
  holdings: Holding[];
  concentration: {
    top_1_pct: number;
    top_3_pct: number;
    top_5_pct: number;
    top_10_pct: number;
    hhi: number;
    effective_holdings: number;
  };
  unpriced_tickers: string[];
  benchmark_note: string;
}

/** Sequential ramp: one hue, descending opacity by rank. Index 0 is the
 * largest slice. Past the 6th the step floors out — beyond that the
 * differences aren't readable and the value label carries the meaning. */
const RAMP = [1, 0.82, 0.66, 0.53, 0.42, 0.34];

function rampOpacity(index: number): number {
  return RAMP[Math.min(index, RAMP.length - 1)];
}

const HUE = "56 152 236"; // single sequential hue, legible on light + dark

interface BarRowProps {
  label: string;
  pct: number;
  value: number;
  currency: string;
  index: number;
  sublabel?: string;
  benchmarkPct?: number | null;
  deltaPp?: number | null;
  tooltip?: string;
  muted?: boolean;
}

function BarRow({
  label, pct, value, currency, index, sublabel,
  benchmarkPct, deltaPp, tooltip, muted,
}: BarRowProps) {
  return (
    <div className="group" title={tooltip}>
      <div className="flex items-baseline justify-between gap-3 mb-1">
        <span className="text-sm truncate">
          {label}
          {sublabel && (
            <span className="ml-1.5 text-[11px] text-muted-foreground">{sublabel}</span>
          )}
        </span>
        <span className="text-sm tabular-nums shrink-0">
          {pct.toFixed(1)}%
          <span className="ml-2 text-xs text-muted-foreground">
            {formatCurrency(value, currency)}
          </span>
        </span>
      </div>

      {/* Track. 6px thin mark, 4px rounded data-end anchored to the
          baseline at left. The benchmark sits on the same track as a
          reference tick — one scale, never a second axis. */}
      <div className="relative h-1.5 w-full rounded bg-secondary/60">
        <div
          className="absolute inset-y-0 left-0 rounded transition-all duration-300"
          style={{
            width: `${Math.min(pct, 100)}%`,
            backgroundColor: muted
              ? "hsl(var(--muted-foreground) / 0.45)"
              : `rgb(${HUE} / ${rampOpacity(index)})`,
          }}
        />
        {benchmarkPct != null && (
          // Whenever the holding is overweight, this tick lands ON TOP of
          // the filled bar rather than beside it, so it carries a 1.5px
          // ring in the surface colour — without it the marker vanishes
          // into a saturated fill exactly when it matters most.
          <div
            className="absolute -top-1 h-3.5 w-[2px] rounded-full bg-foreground/80"
            style={{
              left: `calc(${Math.min(benchmarkPct, 100)}% - 1px)`,
              boxShadow: "0 0 0 1.5px hsl(var(--card))",
            }}
            title={`S&P 500 ≈ ${benchmarkPct.toFixed(1)}%`}
          />
        )}
      </div>

      {deltaPp != null && (
        <div className="mt-1 text-[11px] text-muted-foreground">
          {deltaPp >= 0 ? "+" : "−"}
          {Math.abs(deltaPp).toFixed(1)} pp vs S&P 500 ({benchmarkPct?.toFixed(1)}%)
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-border bg-card p-3" title={hint}>
      <p className="text-[11px] text-muted-foreground uppercase tracking-wide mb-1">
        {label}
      </p>
      <p className="text-lg font-semibold tabular-nums">{value}</p>
    </div>
  );
}

function SectionTitle({ children, note }: { children: React.ReactNode; note?: string }) {
  return (
    <div className="mb-3">
      <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
        {children}
      </h3>
      {note && <p className="text-[11px] text-muted-foreground/70 mt-0.5">{note}</p>}
    </div>
  );
}

/**
 * Donut for a small part-to-whole split.
 *
 * Used for asset type and nothing else. A donut is only honest when the
 * reader's job is "see that these are parts of one whole" across a few
 * segments — it is actively bad at comparing close values, because arc
 * length at different angles is far harder to judge than bar length.
 * Sector allocation fails both tests: it runs to eleven categories, its
 * middle entries sit within a point or two of each other, and there is
 * nowhere on an arc to put the S&P 500 reference tick that makes the
 * sector view worth reading. Asset type is three or four segments with no
 * benchmark, which is exactly the case a donut serves.
 *
 * Same single-hue sequential ramp as the bars, a 2px surface gap between
 * segments, and every segment direct-labelled — nothing here depends on
 * telling two shades apart.
 */
function Donut({
  groups, currency, total,
}: {
  groups: Group[];
  currency: string;
  total: number;
}) {
  const R = 52;
  const STROKE = 16;
  const C = 2 * Math.PI * R;
  // Surface-coloured gap between adjacent fills, in path units. Skipped
  // when a single segment owns the whole ring — a gap there would render
  // as a stray notch in a solid circle.
  const GAP = groups.length > 1 ? 3 : 0;

  let offset = 0;
  const arcs = groups.map((g, i) => {
    const len = Math.max((g.pct / 100) * C - GAP, 0.5);
    const arc = { g, i, len, offset };
    offset += (g.pct / 100) * C;
    return arc;
  });

  return (
    <div className="flex flex-col sm:flex-row items-center gap-5">
      <div className="relative shrink-0">
        <svg width="140" height="140" viewBox="0 0 140 140" role="img"
             aria-label="Allocation by asset type">
          <circle cx="70" cy="70" r={R} fill="none"
                  stroke="hsl(var(--secondary) / 0.6)" strokeWidth={STROKE} />
          {arcs.map(({ g, i, len, offset: o }) => (
            <circle
              key={g.name}
              cx="70" cy="70" r={R} fill="none"
              stroke={`rgb(${HUE} / ${rampOpacity(i)})`}
              strokeWidth={STROKE}
              strokeDasharray={`${len} ${C - len}`}
              strokeDashoffset={-o}
              transform="rotate(-90 70 70)"
            >
              <title>{`${g.name} — ${g.pct.toFixed(1)}%`}</title>
            </circle>
          ))}
        </svg>
        {/* Centre carries the total, the thing the ring is a whole OF. */}
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
            Total
          </span>
          <span className="text-sm font-semibold tabular-nums">
            {formatCurrency(total, currency)}
          </span>
        </div>
      </div>

      {/* Legend — always present, and it carries the numbers so identity
          never rests on shade alone. */}
      <ul className="w-full space-y-2">
        {groups.map((g, i) => (
          <li key={g.name} className="flex items-baseline gap-2 text-sm">
            <span
              className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-sm"
              style={{ backgroundColor: `rgb(${HUE} / ${rampOpacity(i)})` }}
              aria-hidden
            />
            <span className="flex-1 truncate">
              {g.name.charAt(0).toUpperCase() + g.name.slice(1)}
              <span className="ml-1.5 text-[11px] text-muted-foreground">
                {g.count} name{g.count === 1 ? "" : "s"}
              </span>
            </span>
            <span className="tabular-nums shrink-0">
              {g.pct.toFixed(1)}%
              <span className="ml-2 text-xs text-muted-foreground">
                {formatCurrency(g.value, currency)}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

interface Props {
  portfolioId: number;
  portfolioName?: string;
}

export function AllocationTab({ portfolioId, portfolioName }: Props) {
  // Sector exposure is a whole-book question, so the aggregate is the
  // default view — one portfolio at a time is exactly what hides it.
  const [scope, setScope] = useState<"all" | "one">("all");

  const { data, isLoading, isError } = useQuery<AllocationResponse>({
    queryKey: ["allocation", scope, scope === "one" ? portfolioId : null],
    queryFn: () =>
      portfolioApi
        .allocation(scope === "one" ? { portfolio_id: portfolioId } : {})
        .then((r) => r.data),
    staleTime: 60_000,
  });

  // Mini-bars in the holdings table are scaled against the largest
  // holding, so the top row reads as full. Scaling against the largest
  // sector instead would squash every row of a single-sector book.
  const maxHolding = useMemo(
    () => Math.max(1, ...(data?.holdings ?? []).map((h) => h.pct)),
    [data]
  );

  if (isLoading) {
    return (
      <div className="space-y-4">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-20 rounded-lg border border-border bg-card animate-pulse" />
          ))}
        </div>
        <div className="h-64 rounded-xl border border-border bg-card animate-pulse" />
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="rounded-xl border border-border bg-card p-6 text-sm text-muted-foreground">
        Couldn&apos;t load allocation.
      </div>
    );
  }

  const { concentration: c } = data;
  const empty = data.holdings_count === 0;

  return (
    <div className="space-y-4">
      {/* Filters in one row above the charts */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="inline-flex max-w-full rounded-lg border border-border overflow-hidden text-xs">
          {([
            ["all", "All real portfolios"],
            ["one", portfolioName ? `Just ${portfolioName}` : "This portfolio"],
          ] as const).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setScope(key)}
              className={
                "min-w-0 truncate px-3 py-1.5 transition-colors " +
                (scope === key
                  ? "bg-secondary text-foreground"
                  : "text-muted-foreground hover:text-foreground")
              }
              title={label}
            >
              {label}
            </button>
          ))}
        </div>
        {scope === "all" && data.portfolio_names.length > 0 && (
          <span className="text-[11px] text-muted-foreground">
            {data.portfolio_names.join(" · ")} — paper portfolios excluded
          </span>
        )}
      </div>

      {empty ? (
        <div className="rounded-xl border border-border bg-card p-6 text-sm text-muted-foreground">
          No priced holdings to break down yet.
          {data.unpriced_tickers.length > 0 &&
            " Prices refresh every 5 minutes; check back shortly."}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Stat
              label="Total value"
              value={formatCurrency(data.total_value, data.base_currency)}
              hint="Current market value of priced holdings, converted to one currency."
            />
            <Stat label="Holdings" value={String(data.holdings_count)} />
            <Stat
              label="Effective holdings"
              value={c.effective_holdings.toFixed(1)}
              hint={
                "1 / Herfindahl index. Ten equal positions score 10; ten where one " +
                "is 80% score about 1.5. Reads more honestly than a count of rows."
              }
            />
            <Stat
              label="Largest position"
              value={`${c.top_1_pct.toFixed(1)}%`}
              hint="Share of total value in the single biggest holding."
            />
          </div>

          {data.unpriced_tickers.length > 0 && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 flex gap-2">
              <AlertTriangle className="w-4 h-4 text-amber-500 shrink-0 mt-0.5" />
              <div className="text-xs text-muted-foreground">
                <span className="text-foreground">
                  {data.unpriced_tickers.length} holding
                  {data.unpriced_tickers.length === 1 ? "" : "s"} excluded
                </span>{" "}
                — no cached price or no FX rate, so they can&apos;t be weighed against
                the rest: {data.unpriced_tickers.join(", ")}. Percentages below cover
                everything else.
              </div>
            </div>
          )}

          {/* Sector */}
          <div className="rounded-xl border border-border bg-card p-4">
            <SectionTitle note={data.benchmark_note}>By sector</SectionTitle>
            <div className="space-y-3">
              {data.by_sector.map((g, i) => (
                <BarRow
                  key={g.name}
                  label={g.name}
                  pct={g.pct}
                  value={g.value}
                  currency={data.base_currency}
                  index={i}
                  muted={g.name === "Unclassified"}
                  sublabel={`${g.count} name${g.count === 1 ? "" : "s"}`}
                  benchmarkPct={g.benchmark_pct ?? null}
                  deltaPp={g.vs_benchmark_pp ?? null}
                  tooltip={g.tickers.join(", ")}
                />
              ))}
            </div>
            <p className="mt-4 text-[11px] text-muted-foreground/70">
              The vertical tick on each bar is the S&P 500&apos;s approximate weight in
              that sector. Unclassified covers ETFs, crypto and anything the data
              provider has no sector for — it&apos;s shown rather than dropped so the
              shares still add to 100%.
            </p>
          </div>

          {/* Asset type */}
          {data.by_asset_type.length > 0 && (
            <div className="rounded-xl border border-border bg-card p-4">
              <SectionTitle>By asset type</SectionTitle>
              <Donut
                groups={data.by_asset_type}
                currency={data.base_currency}
                total={data.total_value}
              />
            </div>
          )}

          {/* Single-name concentration */}
          <div className="rounded-xl border border-border bg-card p-4">
            <SectionTitle
              note={`HHI ${c.hhi.toFixed(0)} — sum of squared percentage shares.`}
            >
              Single-name concentration
            </SectionTitle>

            <div className="grid grid-cols-4 gap-3 mb-5">
              {([
                ["Top 1", c.top_1_pct],
                ["Top 3", c.top_3_pct],
                ["Top 5", c.top_5_pct],
                ["Top 10", c.top_10_pct],
              ] as const).map(([label, pct], i) => (
                <div key={label}>
                  <p className="text-[11px] text-muted-foreground mb-1">{label}</p>
                  <p className="text-sm font-semibold tabular-nums mb-1.5">
                    {pct.toFixed(1)}%
                  </p>
                  <div className="h-1.5 w-full rounded bg-secondary/60">
                    <div
                      className="h-full rounded"
                      style={{
                        width: `${Math.min(pct, 100)}%`,
                        // Reversed: these four are cumulative, so the
                        // largest must also be the most saturated or the
                        // ramp contradicts the numbers.
                        backgroundColor: `rgb(${HUE} / ${rampOpacity(3 - i)})`,
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>

            {/* Table view — identity never rests on colour alone. */}
            <div className="overflow-x-auto -mx-4 px-4">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[11px] text-muted-foreground uppercase tracking-wide border-b border-border">
                    <th className="pb-2 font-medium">Ticker</th>
                    <th className="pb-2 font-medium">Sector</th>
                    <th className="pb-2 font-medium text-right">Value</th>
                    <th className="pb-2 font-medium text-right">Share</th>
                  </tr>
                </thead>
                <tbody>
                  {data.holdings.map((h, i) => (
                    <tr key={h.ticker} className="border-b border-border/40 last:border-0">
                      <td className="py-2">
                        <span className="font-medium">{h.ticker}</span>
                        {h.name && (
                          <span className="ml-2 text-xs text-muted-foreground hidden sm:inline">
                            {h.name}
                          </span>
                        )}
                        {h.portfolios.length > 1 && (
                          <span
                            className="ml-2 text-[10px] text-muted-foreground"
                            title={h.portfolios.join(", ")}
                          >
                            ×{h.portfolios.length}
                          </span>
                        )}
                      </td>
                      <td className="py-2 text-xs text-muted-foreground">{h.sector}</td>
                      <td className="py-2 text-right tabular-nums text-xs">
                        {formatCurrency(h.value, data.base_currency)}
                      </td>
                      <td className="py-2 text-right">
                        <div className="flex items-center justify-end gap-2">
                          <span className="tabular-nums">{h.pct.toFixed(1)}%</span>
                          <div className="h-1.5 w-12 rounded bg-secondary/60 shrink-0">
                            <div
                              className="h-full rounded"
                              style={{
                                width: `${Math.min((h.pct / maxHolding) * 100, 100)}%`,
                                backgroundColor: `rgb(${HUE} / ${rampOpacity(i)})`,
                              }}
                            />
                          </div>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
