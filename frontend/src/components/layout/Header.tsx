"use client";

import { useAuthStore } from "@/store/auth";
import { NotificationBell } from "@/components/ui/NotificationBell";

export function Header() {
  const user = useAuthStore((s) => s.user);

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
      </div>
    </header>
  );
}
