import { create } from "zustand";
import { persist } from "zustand/middleware";

export type TradingMode = "real" | "paper";

interface TradingModeState {
  mode: TradingMode;
  setMode: (mode: TradingMode) => void;
}

/**
 * Global toggle in the header — switches Dashboard, Portfolio, and Signals
 * pages between viewing the user's REAL portfolios (broker = manual/ibkr)
 * and their PAPER portfolio (broker = paper). Modes are NEVER combined.
 */
export const useTradingModeStore = create<TradingModeState>()(
  persist(
    (set) => ({
      mode: "real",
      setMode: (mode) => set({ mode }),
    }),
    { name: "jarvis-trading-mode" }
  )
);
