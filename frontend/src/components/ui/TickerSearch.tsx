"use client";

import { useState, useRef, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { marketApi } from "@/lib/api";

interface SearchResult {
  ticker: string;
  name: string | null;
  exchange: string | null;
  type: string | null;
}

// Human-readable exchange labels
const EXCHANGE_LABELS: Record<string, string> = {
  NYQ: "NYSE", NMS: "NASDAQ", NGM: "NASDAQ", NCM: "NASDAQ",
  PCX: "NYSE Arca", ASE: "AMEX", CBT: "CBOT", CME: "CME",
  LSE: "LSE", IOB: "LSE Int'l",
  GER: "XETRA", MUN: "Munich", HAM: "Hamburg", BER: "Berlin", STU: "Stuttgart",
  PAR: "Euronext Paris", AMS: "Euronext Amsterdam", BRU: "Euronext Brussels",
  MIL: "Milan", MCE: "Madrid", SWX: "SIX Swiss",
  TYO: "Tokyo", HKG: "Hong Kong", ASX: "ASX", TSX: "TSX",
  PNK: "OTC Pink",
};

function exchangeLabel(code: string | null) {
  if (!code) return "";
  return EXCHANGE_LABELS[code] ?? code;
}

interface Props {
  value: string;
  onChange: (ticker: string) => void;
  onSelect?: (ticker: string) => void;
  placeholder?: string;
  className?: string;
}

export function TickerSearch({ value, onChange, onSelect, placeholder = "Search ticker or name…", className = "" }: Props) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const { data: results = [], isFetching } = useQuery<SearchResult[]>({
    queryKey: ["ticker-search", value],
    queryFn: () => marketApi.search(value).then((r) => r.data),
    enabled: value.trim().length >= 1,
    staleTime: 30_000,
  });

  // Close dropdown when clicking outside
  useEffect(() => {
    function handler(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  function handleSelect(ticker: string) {
    onChange(ticker);
    setOpen(false);
    onSelect?.(ticker);
  }

  return (
    <div ref={containerRef} className={`relative ${className}`}>
      <input
        value={value}
        onChange={(e) => { onChange(e.target.value.toUpperCase()); setOpen(true); }}
        onFocus={() => value.trim().length >= 1 && setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
          if (e.key === "Enter" && !open) onSelect?.(value);
        }}
        placeholder={placeholder}
        className="w-full px-3 py-2 rounded-md border border-border bg-input text-sm focus:outline-none focus:ring-2 focus:ring-ring"
        autoComplete="off"
      />

      {open && value.trim().length >= 1 && (
        <div className="absolute z-50 mt-1 w-full min-w-[280px] rounded-md border border-border bg-card shadow-lg overflow-hidden">
          {isFetching && (
            <div className="px-3 py-2 text-xs text-muted-foreground">Searching…</div>
          )}
          {!isFetching && results.length === 0 && (
            <div className="px-3 py-2 text-xs text-muted-foreground">
              No results. Try adding an exchange suffix: <span className="font-mono">MBG.DE</span>, <span className="font-mono">BARC.L</span>
            </div>
          )}
          {results.map((r) => (
            <button
              key={r.ticker}
              onMouseDown={(e) => e.preventDefault()} // prevent input blur before click
              onClick={() => handleSelect(r.ticker)}
              className="w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-secondary/60 transition-colors"
            >
              <div className="flex-1 min-w-0">
                <span className="font-mono font-semibold text-sm">{r.ticker}</span>
                {r.name && (
                  <span className="ml-2 text-xs text-muted-foreground truncate">{r.name}</span>
                )}
              </div>
              <div className="flex items-center gap-1 shrink-0">
                {r.exchange && (
                  <span className="text-xs px-1.5 py-0.5 rounded bg-secondary text-muted-foreground">
                    {exchangeLabel(r.exchange)}
                  </span>
                )}
                {r.type && (
                  <span className="text-xs text-muted-foreground capitalize">{r.type?.toLowerCase()}</span>
                )}
              </div>
            </button>
          ))}
          <div className="px-3 py-1.5 border-t border-border/50 text-xs text-muted-foreground bg-secondary/30">
            UK: <span className="font-mono">BARC.L</span> &nbsp;·&nbsp;
            DE: <span className="font-mono">MBG.DE</span> &nbsp;·&nbsp;
            FR: <span className="font-mono">MC.PA</span> &nbsp;·&nbsp;
            JP: <span className="font-mono">7203.T</span>
          </div>
        </div>
      )}
    </div>
  );
}
