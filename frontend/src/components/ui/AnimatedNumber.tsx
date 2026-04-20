"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Extracts a numeric value from a formatted string like "$12,345.67" or "-€1,234.56"
 * Returns the number and the prefix/suffix for reconstruction.
 */
function parseFormattedNumber(str: string): { prefix: string; num: number; suffix: string; decimals: number } | null {
  // Match optional prefix (currency symbol, minus), digits with commas, optional decimal, optional suffix (%)
  const match = str.match(/^([^0-9\-]*-?)([0-9][0-9,]*\.?[0-9]*)(.*)$/);
  if (!match) return null;
  const [, prefix, numStr, suffix] = match;
  const decimals = numStr.includes(".") ? numStr.split(".")[1].length : 0;
  const num = parseFloat(numStr.replace(/,/g, ""));
  if (isNaN(num)) return null;
  return { prefix, num, suffix, decimals };
}

function formatWithCommas(num: number, decimals: number): string {
  const fixed = num.toFixed(decimals);
  const [intPart, decPart] = fixed.split(".");
  const withCommas = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return decPart ? `${withCommas}.${decPart}` : withCommas;
}

interface AnimatedNumberProps {
  value: string;
  className?: string;
  duration?: number;
}

export function AnimatedNumber({ value, className, duration = 600 }: AnimatedNumberProps) {
  const [displayValue, setDisplayValue] = useState(value);
  const prevValue = useRef(value);
  const rafRef = useRef<number>(0);

  useEffect(() => {
    const prev = parseFormattedNumber(prevValue.current);
    const next = parseFormattedNumber(value);
    prevValue.current = value;

    // If either can't be parsed as a number, just set directly (e.g. "••••••", "—")
    if (!prev || !next) {
      setDisplayValue(value);
      return;
    }

    // If the number hasn't changed, skip animation
    if (prev.num === next.num) {
      setDisplayValue(value);
      return;
    }

    const startTime = performance.now();
    const startNum = prev.num;
    const endNum = next.num;

    function animate(now: number) {
      const elapsed = now - startTime;
      const progress = Math.min(elapsed / duration, 1);
      // Ease-out cubic for a nice deceleration feel
      const eased = 1 - Math.pow(1 - progress, 3);
      const current = startNum + (endNum - startNum) * eased;
      setDisplayValue(`${next!.prefix}${formatWithCommas(Math.abs(current), next!.decimals)}${next!.suffix}`);

      if (progress < 1) {
        rafRef.current = requestAnimationFrame(animate);
      } else {
        setDisplayValue(value);
      }
    }

    rafRef.current = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(rafRef.current);
  }, [value, duration]);

  return <span className={className}>{displayValue}</span>;
}
