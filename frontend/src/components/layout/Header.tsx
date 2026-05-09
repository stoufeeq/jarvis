"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { LogOut, Search, Settings } from "lucide-react";
import { useAuthStore } from "@/store/auth";
import { useTradingModeStore } from "@/store/tradingMode";
import { NotificationBell } from "@/components/ui/NotificationBell";
import { StockSearchModal } from "@/components/ui/StockSearchModal";
import { MarketSessionBadge } from "@/components/ui/MarketSessionBadge";

export function Header() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const mode = useTradingModeStore((s) => s.mode);
  const setMode = useTradingModeStore((s) => s.setMode);
  const qc = useQueryClient();
  const [searchOpen, setSearchOpen] = useState(false);

  // Cmd/Ctrl + K opens the search modal globally
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen(true);
      }
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, []);

  function switchMode(newMode: "real" | "paper") {
    setMode(newMode);
    // Invalidate portfolio-related queries so pages refetch with the new mode filter
    qc.invalidateQueries({ queryKey: ["portfolios"] });
    qc.invalidateQueries({ queryKey: ["positions"] });
    qc.invalidateQueries({ queryKey: ["all-positions"] });
  }

  return (
    <header className="shrink-0 flex items-center justify-between gap-3 px-4 md:px-6 border-b border-border bg-card" style={{ paddingTop: "env(safe-area-inset-top)", minHeight: "calc(3.5rem + env(safe-area-inset-top))" }}>
      {/* Left side: logo (mobile) + market session badge */}
      <div className="flex items-center gap-3 min-w-0">
        <Image
          src="/logo.png"
          alt="Jarvis"
          width={100}
          height={28}
          className="object-contain md:hidden logo-adaptive shrink-0"
          priority
        />
        <MarketSessionBadge />
      </div>
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
        {/* Stock search */}
        <button
          onClick={() => setSearchOpen(true)}
          className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
          title="Search stock (⌘K)"
          aria-label="Search stock"
        >
          <Search className="w-4 h-4" />
        </button>
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
      <StockSearchModal open={searchOpen} onClose={() => setSearchOpen(false)} />
    </header>
  );
}
