"use client";

import Link from "next/link";

interface Props {
  ticker: string;
  className?: string;
  /** When inside another Link/button, prevents nested-clickable issues */
  stopPropagation?: boolean;
  children?: React.ReactNode;
}

/**
 * Renders a clickable ticker that navigates to /explore/{ticker}.
 * Drop-in replacement for plain ticker text/span.
 *
 * Usage:
 *   <TickerLink ticker={signal.ticker} className="text-lg font-bold" />
 *
 * If wrapping non-ticker content (like a styled badge), pass children:
 *   <TickerLink ticker="AAPL"><CustomBadge /></TickerLink>
 */
export function TickerLink({ ticker, className, stopPropagation, children }: Props) {
  const handleClick = stopPropagation
    ? (e: React.MouseEvent) => e.stopPropagation()
    : undefined;

  return (
    <Link
      href={`/explore/${ticker.toUpperCase()}`}
      onClick={handleClick}
      className={`hover:text-primary hover:underline underline-offset-2 transition-colors cursor-pointer ${className ?? ""}`}
      title={`View ${ticker} details`}
    >
      {children ?? ticker}
    </Link>
  );
}
