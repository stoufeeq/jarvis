"use client";

import Image from "next/image";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { LogOut, Settings } from "lucide-react";
import { useAuthStore } from "@/store/auth";
import { useTradingModeStore } from "@/store/tradingMode";
import { NotificationBell } from "@/components/ui/NotificationBell";

export function Header() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const mode = useTradingModeStore((s) => s.mode);
  const setMode = useTradingModeStore((s) => s.setMode);
  const qc = useQueryClient();

  function switchMode(newMode: "real" | "paper") {
    setMode(newMode);
    // Invalidate portfolio-related queries so pages refetch with the new mode filter
    qc.invalidateQueries({ queryKey: ["portfolios"] });
    qc.invalidateQueries({ queryKey: ["positions"] });
    qc.invalidateQueries({ queryKey: ["all-positions"] });
  }

  return (
    <header className="shrink-0 flex items-center justify-between gap-3 px-4 md:px-6 border-b border-border bg-card" style={{ paddingTop: "env(safe-area-inset-top)", minHeight: "calc(3.5rem + env(safe-area-inset-top))" }}>
      {/* Logo — visible on mobile where sidebar is hidden */}
      <Image
        src="/logo.png"
        alt="Jarvis"
        width={100}
        height={28}
        className="object-contain md:hidden logo-adaptive"
        priority
      />
      <div className="hidden md:block" /> {/* spacer on desktop */}
      <div className="flex items-center gap-3">
        {/* Real / Paper trading mode toggle */}
        <div className="flex items-center gap-1 p-0.5 rounded-md bg-secondary/50 border border-border">
          <button
            onClick={() => switchMode("real")}
            className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
              mode === "real"
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            }`}
            title="Show your real portfolios"
          >
            Real
          </button>
          <button
            onClick={() => switchMode("paper")}
            className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
              mode === "paper"
                ? "bg-amber-500/20 text-amber-500 shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            }`}
            title="Show your paper trading portfolio"
          >
            Paper
          </button>
        </div>
        <NotificationBell />
        <Link
          href="/settings"
          className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
          title="Settings"
        >
          <Settings className="w-4 h-4" />
        </Link>
        <span className="text-sm text-muted-foreground hidden sm:inline">
          {user?.full_name ?? user?.email ?? ""}
        </span>
        {/* Sign out — mobile only (desktop has it in the sidebar) */}
        <button
          onClick={logout}
          className="md:hidden p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
          title="Sign out"
        >
          <LogOut className="w-4 h-4" />
        </button>
      </div>
    </header>
  );
}
