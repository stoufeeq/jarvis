import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const CURRENCY_SYMBOLS: Record<string, string> = {
  SGD: "S$",
  HKD: "HK$",
  CAD: "CA$",
  AUD: "A$",
  NZD: "NZ$",
};

/** Returns a short display label for a currency code, e.g. "SGD" → "S$", "USD" → "$". */
export function currencyLabel(currency: string): string {
  if (CURRENCY_SYMBOLS[currency]) return CURRENCY_SYMBOLS[currency];
  // For well-known symbols the browser can resolve (USD→$, EUR→€, GBP→£, JPY→¥)
  try {
    const parts = new Intl.NumberFormat("en-US", { style: "currency", currency })
      .formatToParts(0);
    const sym = parts.find((p) => p.type === "currency")?.value;
    if (sym && sym !== currency) return sym;
  } catch {
    // ignore
  }
  return currency;
}

export function formatCurrency(value: number | null | undefined, currency = "USD"): string {
  if (value == null) return "—";
  // For currencies where Intl outputs the ISO code (e.g. "SGD 1,234.56") we
  // format as a plain number and prepend our preferred symbol instead.
  if (CURRENCY_SYMBOLS[currency]) {
    const num = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
    return `${CURRENCY_SYMBOLS[currency]}${num}`;
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
  }).format(value);
}

export function formatPct(value: number | null | undefined): string {
  if (value == null) return "—";
  const sign = value >= 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

export function formatNumber(value: number | null | undefined): string {
  if (value == null) return "—";
  return new Intl.NumberFormat("en-US").format(value);
}

export function pnlColor(value: number | null | undefined): string {
  if (value == null) return "text-muted-foreground";
  return value >= 0 ? "text-emerald-500" : "text-red-500";
}
