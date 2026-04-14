import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
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

  const { data: fxData } = useQuery({
    queryKey: ["fx", base, displayCurrency],
    queryFn: () => marketApi.fx(base, displayCurrency).then((r) => r.data),
    enabled: needsConversion,
    staleTime: 60_000,
    refetchInterval: 60_000,
  });

  const rate: number = needsConversion ? (fxData?.rate ?? 1) : 1;

  function convert(value: number | null | undefined): number | null {
    if (value == null) return null;
    return value * rate;
  }

  return { displayCurrency, setDisplayCurrency, rate, convert, base };
}
