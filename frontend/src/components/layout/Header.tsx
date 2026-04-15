"use client";

import { LogOut } from "lucide-react";
import { useAuthStore } from "@/store/auth";
import { NotificationBell } from "@/components/ui/NotificationBell";

export function Header() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);

  return (
    <header className="h-14 shrink-0 flex items-center justify-between gap-3 px-4 md:px-6 border-b border-border bg-card">
      {/* App name — visible on mobile where sidebar is hidden */}
      <span className="text-base font-bold tracking-tight md:hidden">
        Jarvis <span className="text-xs font-normal text-muted-foreground">beta</span>
      </span>
      <div className="hidden md:block" /> {/* spacer on desktop */}
      <div className="flex items-center gap-3">
        <NotificationBell />
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
