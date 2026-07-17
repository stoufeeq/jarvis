"use client";

/**
 * Header-mounted button that surfaces recent error toasts so they can
 * be re-read after auto-dismiss. Unread count badges the icon; click
 * opens a dropdown listing each entry with timestamp and per-entry
 * dismiss, plus bulk "Mark all read" / "Clear all" actions.
 *
 * Errors are recorded via the `errorToast` helper in lib/notify.ts —
 * call sites that used to do `toast.error(msg)` should use that
 * instead so the message gets logged.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, X } from "lucide-react";

import { useErrorLogStore, unreadErrorCount } from "@/store/errorLog";

function formatAge(ts: number): string {
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

export function ErrorLogButton() {
  const [open, setOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);

  const entries = useErrorLogStore((s) => s.entries);
  const markAllRead = useErrorLogStore((s) => s.markAllRead);
  const clearAll = useErrorLogStore((s) => s.clearAll);
  const remove = useErrorLogStore((s) => s.remove);

  const unread = useMemo(() => unreadErrorCount(entries), [entries]);

  // Close on outside click.
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    if (open) document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  // Mark all as read shortly after the panel opens so the badge clears
  // once the user has actually seen the panel content — instant clear
  // on open would race the first render.
  useEffect(() => {
    if (!open || unread === 0) return;
    const t = setTimeout(() => markAllRead(), 400);
    return () => clearTimeout(t);
  }, [open, unread, markAllRead]);

  return (
    <div className="relative" ref={panelRef}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="relative p-2 rounded-md hover:bg-secondary transition-colors"
        aria-label="Recent errors"
        title="Recent errors"
      >
        <AlertCircle className={`w-4 h-4 ${unread > 0 ? "text-red-400" : "text-muted-foreground"}`} />
        {unread > 0 && (
          <span className="absolute top-1 right-1 w-4 h-4 rounded-full bg-red-500 text-white text-[10px] font-bold flex items-center justify-center leading-none">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-80 rounded-xl border border-border bg-card shadow-xl z-50 overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-border">
            <span className="text-sm font-semibold">Recent errors</span>
            {entries.length > 0 && (
              <button
                onClick={() => clearAll()}
                className="text-xs text-muted-foreground hover:text-foreground"
              >
                Clear all
              </button>
            )}
          </div>

          {entries.length === 0 ? (
            <div className="px-4 py-6 text-center text-sm text-muted-foreground">
              No errors recorded.
            </div>
          ) : (
            <ul className="max-h-96 overflow-y-auto divide-y divide-border">
              {entries.map((e) => (
                <li key={e.id} className="px-4 py-3 flex items-start gap-3 group">
                  <div className={`mt-1 h-2 w-2 rounded-full shrink-0 ${e.read ? "bg-muted-foreground/30" : "bg-red-500"}`} />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-foreground break-words">{e.message}</p>
                    <p className="mt-0.5 text-[11px] text-muted-foreground">{formatAge(e.timestamp)}</p>
                  </div>
                  <button
                    onClick={() => remove(e.id)}
                    className="opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-secondary"
                    aria-label="Dismiss"
                  >
                    <X className="w-3.5 h-3.5 text-muted-foreground" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
