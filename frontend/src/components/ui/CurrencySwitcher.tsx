"use client";

import { DISPLAY_CURRENCIES } from "@/hooks/useCurrencyDisplay";

interface Props {
  base: string;
  display: string;
  rate: number;
  onChange: (currency: string) => void;
}

export function CurrencySwitcher({ base, display, rate, onChange }: Props) {
  const isConverted = display !== base;

  return (
    <div className="flex items-center gap-2">
      {isConverted && (
        <span className="text-xs text-muted-foreground">
          1 {base} = {rate.toFixed(4)} {display}
        </span>
      )}
      <select
        value={display}
        onChange={(e) => onChange(e.target.value)}
        className="px-2 py-1 rounded-md border border-border bg-input text-xs focus:outline-none focus:ring-2 focus:ring-ring"
      >
        {DISPLAY_CURRENCIES.map((c) => (
          <option key={c} value={c}>
            {c}{c === base ? " (base)" : ""}
          </option>
        ))}
      </select>
    </div>
  );
}
