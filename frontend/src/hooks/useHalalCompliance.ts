"use client";

import { useQuery } from "@tanstack/react-query";
import { halalApi } from "@/lib/api";
import { useSettingsStore } from "@/store/settings";

export type HalalStatus = "compliant" | "non_compliant" | "unknown";

export interface HalalCompliance {
  ticker: string;
  status: HalalStatus;
  reason: string | null;
  quote_type: string | null;
  sector: string | null;
  industry: string | null;
  debt_pct: number | null;
  cash_pct: number | null;
  computed_at: string;
}

/**
 * Batch-fetch compliance verdicts for a list of tickers. Query is disabled
 * when halal mode is off so we don't waste calls. Cache for 6h client-side
 * (backend has its own 24h TTL).
 */
export function useHalalCompliance(tickers: string[]) {
  const halalMode = useSettingsStore((s) => s.halalMode);
  const halalOnlyFilter = useSettingsStore((s) => s.halalOnlyFilter);

  // Stable key — sort & dedupe so order/extras don't bust the cache.
  const key = Array.from(new Set(tickers.map((t) => t.toUpperCase()))).sort();
  const enabled = (halalMode || halalOnlyFilter) && key.length > 0;

  const { data } = useQuery<HalalCompliance[]>({
    queryKey: ["halal", key],
    queryFn: () => halalApi.many(key).then((r) => r.data),
    enabled,
    staleTime: 6 * 60 * 60 * 1000,
  });

  const byTicker: Record<string, HalalCompliance> = {};
  for (const row of data ?? []) byTicker[row.ticker] = row;
  return byTicker;
}
