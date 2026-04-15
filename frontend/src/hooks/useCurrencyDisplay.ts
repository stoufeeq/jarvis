import { useState, useEffect, useRef } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { marketApi } from "@/lib/api";

export const DISPLAY_CURRENCIES = ["USD", "EUR", "GBP", "SGD", "JPY", "CAD", "AUD", "CHF", "HKD", "INR"] as const;

const STORAGE_KEY = "jarvis_display_currency";

export function useCurrencyDisplay(baseCurrency: string = "USD") {
  const base = (baseCurrency || "USD").toUpperCase();

  const [displayCurrency, setDisplayCurrencyState] = useState<string>(() => {
    // Read persisted value on first render (client-side only)
    if (typeof window !== "undefined") {
      return localStorage.getItem(STORAGE_KEY) ?? base;
    }
    return base;
  });

  // If baseCurrency prop changes (e.g. different portfolio selected) and
  // no persisted preference exists, follow the new base.
  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) setDisplayCurrencyState(base);
  }, [base]);

  function setDisplayCurrency(currency: string) {
    const upper = currency.toUpperCase();
    localStorage.setItem(STORAGE_KEY, upper);
    setDisplayCurrencyState(upper);
  }

  const needsConversion = displayCurrency !== base;

  // Remember the last successfully fetched rate so a timeout/error doesn't
  // reset everything to 1:1. Reset to 1 when the currency pair changes so we
  // don't bleed the old rate into a different pair before the first fetch lands.
  const lastKnownRate = useRef<number>(1);
  const lastPair = useRef<string>(`${base}/${displayCurrency}`);
  const currentPair = `${base}/${displayCurrency}`;
  if (currentPair !== lastPair.current) {
    lastKnownRate.current = 1;
    lastPair.current = currentPair;
  }

  const { data: fxData } = useQuery({
    queryKey: ["fx", base, displayCurrency],
    queryFn: () => marketApi.fx(base, displayCurrency).then((r) => r.data),
    enabled: needsConversion,
    staleTime: 60_000,
    refetchInterval: 60_000,
    placeholderData: keepPreviousData, // keep old data visible during refetch
    retry: 2,
  });

  // Update the ref whenever we get a fresh rate
  if (fxData?.rate && fxData.rate !== lastKnownRate.current) {
    lastKnownRate.current = fxData.rate;
  }

  const rate: number = needsConversion ? (fxData?.rate ?? lastKnownRate.current) : 1;

  function convert(value: number | null | undefined): number | null {
    if (value == null) return null;
    return value * rate;
  }

  return { displayCurrency, setDisplayCurrency, rate, convert, base };
}
