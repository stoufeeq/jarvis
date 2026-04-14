"use client";

import { useAuthStore } from "@/store/auth";
import { NotificationBell } from "@/components/ui/NotificationBell";

export function Header() {
  const user = useAuthStore((s) => s.user);

  return (
    <header className="h-14 shrink-0 flex items-center justify-end gap-3 px-6 border-b border-border bg-card">
      <NotificationBell />
      <span className="text-sm text-muted-foreground">
        {user?.full_name ?? user?.email ?? ""}
      </span>
    </header>
  );
}
