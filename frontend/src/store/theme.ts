import { create } from "zustand";
import { persist } from "zustand/middleware";

export type ThemeKey =
  | "dark"
  | "midnight"
  | "slate"
  | "forest"
  | "rose"
  | "light"
  | "sepia"
  | "ocean";

export interface ThemeDefinition {
  key: ThemeKey;
  label: string;
  dark: boolean;         // true = light text; false = dark text
  preview: string;       // CSS color for the swatch
}

export const THEMES: ThemeDefinition[] = [
  { key: "dark",     label: "Dark",     dark: true,  preview: "#0d1526" },
  { key: "midnight", label: "Midnight", dark: true,  preview: "#080808" },
  { key: "slate",    label: "Slate",    dark: true,  preview: "#0f1720" },
  { key: "forest",   label: "Forest",   dark: true,  preview: "#081510" },
  { key: "rose",     label: "Rose",     dark: true,  preview: "#160a0e" },
  { key: "light",    label: "Light",    dark: false, preview: "#f8f9fa" },
  { key: "sepia",    label: "Sepia",    dark: false, preview: "#f5f0e8" },
  { key: "ocean",    label: "Ocean",    dark: false, preview: "#eef4fb" },
];

interface ThemeStore {
  theme: ThemeKey;
  setTheme: (t: ThemeKey) => void;
}

export const useThemeStore = create<ThemeStore>()(
  persist(
    (set) => ({
      theme: "dark",
      setTheme: (theme) => set({ theme }),
    }),
    { name: "jarvis_theme" }
  )
);
