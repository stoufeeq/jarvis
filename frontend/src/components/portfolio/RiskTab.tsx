"use client";

/**
 * Portfolio Risk analytics tab.
 *
 * Shows portfolio-level quant metrics (Sharpe, MDD, vol, beta, alpha)
 * plus a correlation matrix of the top holdings. Metrics are unitless
 * or in %, so no currency conversion is done — the numbers are the
 * same regardless of what the CurrencySwitcher shows.
 *
 * Skeleton renders while the backend crunches (takes a few seconds
 * because it fetches per-holding price history for the matrix).
 */
import { useQuery } from "@tanstack/react-query";
import { portfolioApi } from "@/lib/api";

interface RiskResponse {
  sharpe_30d: number | null;
  sharpe_90d: number | null;
  sharpe_365d: number | null;
  max_drawdown_pct: number | null;
  volatility_30d_pct: number | null;
  beta_spy: number | null;
  alpha_90d_pct: number | null;
  correlation: {
    tickers: string[];
    matrix: number[][];
  };
  diversification_score: number | null;
  returns_period_days: number;
}

/** Sharpe interpretation coloured for a quick read. */
function sharpeColour(s: number | null): string {
  if (s == null) return "text-muted-foreground";
  if (s >= 1.5) return "text-emerald-500";
  if (s >= 1.0) return "text-emerald-400";
  if (s >= 0.5) return "text-amber-500";
  if (s >= 0) return "text-amber-600";
  return "text-red-500";
}

function fmtRatio(v: number | null, decimals = 2): string {
  return v == null ? "—" : v.toFixed(decimals);
}

function fmtPct(v: number | null): string {
  if (v == null) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

/** Correlation → cell background. Green (uncorrelated), amber, red. */
function corrCellStyle(v: number): { bg: string; text: string } {
  const abs = Math.abs(v);
  if (abs >= 0.99) return { bg: "bg-secondary/60", text: "text-muted-foreground" };
  if (abs >= 0.7) return { bg: "bg-red-500/25", text: "text-red-300" };
  if (abs >= 0.3) return { bg: "bg-amber-500/20", text: "text-amber-300" };
  return { bg: "bg-emerald-500/15", text: "text-emerald-300" };
}

interface Props {
  portfolioId: number;
}

export function RiskTab({ portfolioId }: Props) {
  const { data, isLoading, error } = useQuery<RiskResponse>({
    queryKey: ["portfolio-risk", portfolioId],
    queryFn: () => portfolioApi.risk(portfolioId).then((r) => r.data),
    // Same cadence as the performance chart — history is only meaningfully
    // updated at daily close, and per-ticker fetches are expensive.
    staleTime: 10 * 60 * 1000,
  });

  if (isLoading) {
    return (
      <div className="p-8 text-sm text-muted-foreground text-center">
        Computing risk metrics… (fetches per-holding price history)
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="p-6 text-sm text-red-400">
        Failed to load risk metrics. Try again in a moment.
      </div>
    );
  }

  if (data.returns_period_days < 5) {
    return (
      <div className="rounded-lg border border-border/50 bg-card p-6 text-sm text-muted-foreground">
        Not enough return history yet ({data.returns_period_days} day{data.returns_period_days === 1 ? "" : "s"}).
        Metrics need at least ~10 days of equity-curve data to be meaningful.
      </div>
    );
  }

  const tickers = data.correlation.tickers;
  const matrix = data.correlation.matrix;

  return (
    <div className="space-y-6">
      {/* ── Stat grid ────────────────────────────────────────────── */}
      <div>
        <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide mb-3">
          Risk metrics
        </h3>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          <Metric
            label="Sharpe 30d"
            value={fmtRatio(data.sharpe_30d)}
            valueClass={sharpeColour(data.sharpe_30d)}
            hint="Return per unit of risk, annualised. >1 decent, >1.5 strong."
          />
          <Metric
            label="Sharpe 90d"
            value={fmtRatio(data.sharpe_90d)}
            valueClass={sharpeColour(data.sharpe_90d)}
            hint="Same, over the trailing 3 months."
          />
          <Metric
            label="Sharpe 365d"
            value={fmtRatio(data.sharpe_365d)}
            valueClass={sharpeColour(data.sharpe_365d)}
            hint="Trailing year. Most meaningful once you have a full year of data."
          />
          <Metric
            label="Max Drawdown"
            value={fmtPct(data.max_drawdown_pct)}
            valueClass={(data.max_drawdown_pct ?? 0) <= -20 ? "text-red-500"
              : (data.max_drawdown_pct ?? 0) <= -10 ? "text-amber-500"
              : "text-emerald-400"}
            hint="Worst peak-to-trough drop on the equity curve."
          />
          <Metric
            label="Volatility (30d)"
            value={fmtPct(data.volatility_30d_pct)}
            valueClass="text-foreground"
            hint="Annualised std-dev of recent daily returns."
          />
          <Metric
            label="Beta vs SPY"
            value={fmtRatio(data.beta_spy)}
            valueClass="text-foreground"
            hint="1.0 = moves with the market. >1 = amplified. <0 = inverse."
          />
          <Metric
            label="Alpha (90d)"
            value={fmtPct(data.alpha_90d_pct)}
            valueClass={(data.alpha_90d_pct ?? 0) > 0 ? "text-emerald-500" : "text-red-500"}
            hint="Return in excess of what your beta implies. Positive = beating the market on a risk-adjusted basis."
          />
          <Metric
            label="Diversification"
            value={data.diversification_score != null
              ? data.diversification_score.toFixed(2)
              : "—"}
            valueClass={(data.diversification_score ?? 1) >= 0.7 ? "text-red-400"
              : (data.diversification_score ?? 1) >= 0.4 ? "text-amber-400"
              : "text-emerald-400"}
            hint="Avg pairwise correlation of top holdings. 0 = diversified, 1 = all one bet."
          />
        </div>
      </div>

      {/* ── Correlation matrix ──────────────────────────────────── */}
      {tickers.length >= 2 ? (
        <div>
          <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide mb-3">
            Position correlation (top {tickers.length} holdings, 90d returns)
          </h3>
          <div className="rounded-lg border border-border bg-card overflow-x-auto">
            <table className="text-xs w-full">
              <thead>
                <tr>
                  <th className="p-2 text-left font-semibold text-muted-foreground border-b border-border sticky left-0 bg-card z-10">
                    &nbsp;
                  </th>
                  {tickers.map((t) => (
                    <th
                      key={t}
                      className="p-2 text-center font-semibold text-muted-foreground border-b border-border"
                    >
                      {t}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {tickers.map((rowTicker, i) => (
                  <tr key={rowTicker}>
                    <td className="p-2 font-semibold text-muted-foreground border-b border-border sticky left-0 bg-card z-10">
                      {rowTicker}
                    </td>
                    {tickers.map((colTicker, j) => {
                      const v = matrix[i]?.[j] ?? 0;
                      const style = corrCellStyle(v);
                      return (
                        <td
                          key={colTicker}
                          className={`p-2 text-center border-b border-border ${style.bg} ${style.text} tabular-nums`}
                          title={`${rowTicker} vs ${colTicker}: ${v.toFixed(3)}`}
                        >
                          {v.toFixed(2)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[11px] text-muted-foreground">
            Green = uncorrelated (safer to hold together). Amber = mildly correlated.
            Red = highly correlated (concentration risk — a shock to one usually hits the others).
          </p>
        </div>
      ) : (
        <div className="rounded-lg border border-border/50 bg-card p-4 text-sm text-muted-foreground">
          Correlation matrix needs at least 2 holdings with price data.
        </div>
      )}
    </div>
  );
}

function Metric({
  label,
  value,
  valueClass,
  hint,
}: {
  label: string;
  value: string;
  valueClass?: string;
  hint?: string;
}) {
  return (
    <div className="rounded-lg border border-border/50 bg-card p-3">
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className={`text-lg font-semibold tabular-nums mt-0.5 ${valueClass ?? ""}`}>
        {value}
      </p>
      {hint && (
        <p className="text-[10px] text-muted-foreground/70 mt-1 leading-tight">{hint}</p>
      )}
    </div>
  );
}
