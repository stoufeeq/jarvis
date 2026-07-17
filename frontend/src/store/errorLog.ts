/**
 * Persistent error log — captures error toasts so they can be
 * re-read after the toast auto-dismisses. Backing store is localStorage
 * so entries survive refresh, and the drop-down icon in the header
 * shows the unread count as a badge.
 *
 * Cap at MAX_ENTRIES so the log doesn't grow unbounded — oldest entries
 * fall off the tail when the cap is hit.
 */
import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface ErrorEntry {
  id: string;
  message: string;
  timestamp: number;
  read: boolean;
}

interface ErrorLogState {
  entries: ErrorEntry[];
  logError: (message: string) => void;
  markAllRead: () => void;
  clearAll: () => void;
  remove: (id: string) => void;
}

const MAX_ENTRIES = 50;

export const useErrorLogStore = create<ErrorLogState>()(
  persist(
    (set) => ({
      entries: [],
      logError: (message) =>
        set((state) => {
          const entry: ErrorEntry = {
            // Random-enough id: timestamp + small random suffix (no crypto
            // needed — collisions across a 50-entry ring don't matter).
            id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
            message,
            timestamp: Date.now(),
            read: false,
          };
          const next = [entry, ...state.entries];
          if (next.length > MAX_ENTRIES) next.length = MAX_ENTRIES;
          return { entries: next };
        }),
      markAllRead: () =>
        set((state) => ({
          entries: state.entries.map((e) => (e.read ? e : { ...e, read: true })),
        })),
      clearAll: () => set({ entries: [] }),
      remove: (id) =>
        set((state) => ({ entries: state.entries.filter((e) => e.id !== id) })),
    }),
    { name: "jarvis-error-log" },
  ),
);

export function unreadErrorCount(entries: ErrorEntry[]): number {
  return entries.reduce((n, e) => n + (e.read ? 0 : 1), 0);
}
