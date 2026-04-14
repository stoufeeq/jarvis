import { create } from "zustand";
import { persist } from "zustand/middleware";

interface PrivacyState {
  isPrivate: boolean;
  setPrivate: (v: boolean) => void;
}

export const usePrivacyStore = create<PrivacyState>()(
  persist(
    (set) => ({
      isPrivate: false,
      setPrivate: (v) => set({ isPrivate: v }),
    }),
    { name: "jarvis-privacy" }
  )
);
