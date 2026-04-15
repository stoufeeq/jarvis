"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Briefcase,
  BookOpen,
  TrendingUp,
  Bell,
  MessageSquare,
} from "lucide-react";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/dashboard", label: "Home", icon: LayoutDashboard },
  { href: "/portfolio", label: "Portfolio", icon: Briefcase },
  { href: "/watchlist", label: "Watchlist", icon: BookOpen },
  { href: "/signals", label: "Signals", icon: TrendingUp },
  { href: "/alerts", label: "Alerts", icon: Bell },
  { href: "/advisor", label: "Advisor", icon: MessageSquare },
];

export function BottomNav() {
  const pathname = usePathname();

  return (
    <nav className="md:hidden fixed bottom-0 inset-x-0 z-40 flex border-t border-border bg-card">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            className={cn(
              "flex-1 flex flex-col items-center justify-center gap-0.5 py-2 text-[10px] font-medium transition-colors",
              active ? "text-primary" : "text-muted-foreground"
            )}
          >
            <Icon className={cn("w-5 h-5", active ? "text-primary" : "text-muted-foreground")} />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
