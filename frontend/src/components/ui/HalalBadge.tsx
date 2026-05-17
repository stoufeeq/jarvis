"use client";

import { Check, X, HelpCircle } from "lucide-react";
import type { HalalCompliance } from "@/hooks/useHalalCompliance";
import { useSettingsStore } from "@/store/settings";
import { cn } from "@/lib/utils";

interface Props {
  compliance: HalalCompliance | undefined;
  className?: string;
}

// Neutral palette — distinct from price red/green so it reads as a
// separate signal from P&L colour-coding.
const STYLE: Record<string, { icon: typeof Check; color: string; label: string }> = {
  compliant:     { icon: Check,      color: "text-blue-400 border-blue-400/40 bg-blue-400/10",   label: "Halal" },
  non_compliant: { icon: X,          color: "text-orange-400 border-orange-400/40 bg-orange-400/10", label: "Not halal" },
  unknown:       { icon: HelpCircle, color: "text-slate-400 border-slate-400/30 bg-slate-400/10",  label: "Unknown" },
};

/**
 * Tiny inline icon to the right of a ticker. Renders nothing when halal
 * mode is off, or when the compliance row hasn't loaded yet.
 */
export function HalalBadge({ compliance, className }: Props) {
  const halalMode = useSettingsStore((s) => s.halalMode);
  if (!halalMode || !compliance) return null;

  const style = STYLE[compliance.status] ?? STYLE.unknown;
  const Icon = style.icon;
  const tooltip = compliance.reason
    ? `${style.label} — ${compliance.reason}`
    : style.label;

  return (
    <span
      title={tooltip}
      className={cn(
        "inline-flex items-center justify-center w-4 h-4 rounded-full border",
        style.color,
        className
      )}
      aria-label={tooltip}
    >
      <Icon className="w-2.5 h-2.5" strokeWidth={3} />
    </span>
  );
}
