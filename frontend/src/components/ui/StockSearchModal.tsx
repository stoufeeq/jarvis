"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Search, X } from "lucide-react";
import { marketApi } from "@/lib/api";

interface SearchResult {
  ticker: string;
  name: string;
  exchange: string | null;
  type: string | null;
}

interface Props {
  open: boolean;
  onClose: () => void;
}

/**
 * Modal that searches across yfinance + curated crypto list and navigates
 * to /explore/{ticker} on selection. Triggered by the search icon in the
 * header. Keyboard navigation: ↑/↓ to move, Enter to select, Esc to close.
 */
export function StockSearchModal({ open, onClose }: Props) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [highlightIdx, setHighlightIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Reset state and focus input when modal opens
  useEffect(() => {
    if (open) {
      setQuery("");
      setResults([]);
      setHighlightIdx(0);
      // Wait one tick so the input exists before focusing
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  // Debounced search
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!query.trim()) {
      setResults([]);
      return;
    }
    debounceRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        const res = await marketApi.search(query.trim());
        setResults(res.data ?? []);
        setHighlightIdx(0);
      } catch {
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, 250);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query]);

  // Keyboard navigation
  useEffect(() => {
    if (!open) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onClose();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setHighlightIdx((i) => Math.min(results.length - 1, i + 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setHighlightIdx((i) => Math.max(0, i - 1));
      } else if (e.key === "Enter") {
        if (results[highlightIdx]) {
          select(results[highlightIdx].ticker);
        }
      }
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, results, highlightIdx]);

  function select(ticker: string) {
    onClose();
    router.push(`/explore/${ticker.toUpperCase()}`);
  }

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 backdrop-blur-sm pt-20 px-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-xl rounded-xl border border-border bg-card shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Search input */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
          <Search className="w-4 h-4 text-muted-foreground" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search ticker or company (e.g. AAPL, NVDA, Bitcoin)…"
            className="flex-1 bg-transparent outline-none text-sm placeholder:text-muted-foreground/60"
          />
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground transition-colors"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Results */}
        <div className="max-h-96 overflow-y-auto">
          {loading && (
            <p className="px-4 py-3 text-sm text-muted-foreground">Searching…</p>
          )}
          {!loading && query.trim() && results.length === 0 && (
            <p className="px-4 py-3 text-sm text-muted-foreground">No matches.</p>
          )}
          {!loading && !query.trim() && (
            <p className="px-4 py-6 text-center text-xs text-muted-foreground/70">
              Type a ticker or company name to look up any stock or crypto.
              <br />
              <span className="opacity-60">Use ↑/↓ to navigate, Enter to open.</span>
            </p>
          )}
          {results.map((r, i) => (
            <button
              key={`${r.ticker}-${i}`}
              onClick={() => select(r.ticker)}
              onMouseEnter={() => setHighlightIdx(i)}
              className={`w-full px-4 py-3 flex items-center gap-3 text-left transition-colors ${
                i === highlightIdx ? "bg-secondary/60" : "hover:bg-secondary/30"
              }`}
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-sm">{r.ticker}</span>
                  {r.type === "crypto" && (
                    <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-500">
                      crypto
                    </span>
                  )}
                  {r.exchange && (
                    <span className="text-[10px] text-muted-foreground/60">{r.exchange}</span>
                  )}
                </div>
                <p className="text-xs text-muted-foreground truncate">{r.name}</p>
              </div>
              <span className="text-xs text-muted-foreground/50">↵</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
