"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Briefcase,
  BookOpen,
  TrendingUp,
  Bell,
  Wallet,
  LayoutGrid,
  Newspaper,
  CalendarDays,
  Cpu,
} from "lucide-react";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/dashboard", label: "Home", icon: LayoutDashboard },
  { href: "/briefing", label: "Briefing", icon: Newspaper },
  { href: "/portfolio", label: "Portfolio", icon: Briefcase },
  { href: "/accounts", label: "Accounts", icon: Wallet },
  { href: "/watchlist", label: "Watchlist", icon: BookOpen },
  { href: "/signals", label: "Signals", icon: TrendingUp },
  { href: "/strategies", label: "Auto", icon: Cpu },
  { href: "/calendar", label: "Calendar", icon: CalendarDays },
  { href: "/heatmap", label: "Heatmap", icon: LayoutGrid },
  { href: "/alerts", label: "Alerts", icon: Bell },
];

export function BottomNav() {
  const pathname = usePathname();

  return (
    <nav className="md:hidden fixed bottom-0 inset-x-0 z-40 flex border-t border-border bg-card overflow-x-auto" style={{ paddingBottom: "env(safe-area-inset-bottom)" }}>
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            className={cn(
              "flex-1 flex flex-col items-center justify-center gap-0.5 py-2 text-[9px] font-medium transition-colors min-w-[50px]",
              active ? "text-primary" : "text-muted-foreground"
            )}
          >
            <Icon className={cn("w-4 h-4", active ? "text-primary" : "text-muted-foreground")} />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
