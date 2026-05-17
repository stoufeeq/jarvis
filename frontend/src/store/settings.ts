import { create } from "zustand";
import { persist } from "zustand/middleware";

interface SettingsState {
  // Show halal compliance badges next to tickers.
  halalMode: boolean;
  // Hide non-compliant entries on filterable list pages (Watchlist for now;
  // Portfolio still shows everything — you can't hide what you already own).
  halalOnlyFilter: boolean;
  setHalalMode: (v: boolean) => void;
  setHalalOnlyFilter: (v: boolean) => void;
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      halalMode: false,
      halalOnlyFilter: false,
      setHalalMode: (v) => set({ halalMode: v }),
      setHalalOnlyFilter: (v) => set({ halalOnlyFilter: v }),
    }),
    { name: "jarvis-settings" }
  )
);
