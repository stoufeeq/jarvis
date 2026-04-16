"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Briefcase,
  TrendingUp,
  Bell,
  BookOpen,
  MessageSquare,
  LogOut,
  Wallet,
  LayoutGrid,
  Newspaper,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/store/auth";

const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/briefing", label: "Briefing", icon: Newspaper },
  { href: "/portfolio", label: "Portfolio", icon: Briefcase },
  { href: "/accounts", label: "Accounts", icon: Wallet },
  { href: "/watchlist", label: "Watchlist", icon: BookOpen },
  { href: "/signals", label: "Signals", icon: TrendingUp },
  { href: "/heatmap", label: "Heatmap", icon: LayoutGrid },
  { href: "/alerts", label: "Alerts", icon: Bell },
  { href: "/advisor", label: "AI Advisor", icon: MessageSquare },
];

export function Sidebar() {
  const pathname = usePathname();
  const logout = useAuthStore((s) => s.logout);

  return (
    <aside className="w-56 shrink-0 flex flex-col border-r border-border bg-card">
      <div className="px-4 py-3 flex items-center">
        <Image
          src="/logo.png"
          alt="Jarvis"
          width={98}
          height={27}
          className="object-contain logo-adaptive"
          priority
        />
      </div>

      <nav className="flex-1 px-3 py-4 space-y-1">
        {NAV.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            className={cn(
              "flex items-center gap-2.5 px-3 py-2 rounded-md text-sm font-medium transition-colors",
              pathname.startsWith(href)
                ? "bg-secondary text-foreground"
                : "text-muted-foreground hover:bg-secondary hover:text-foreground"
            )}
          >
            <Icon className="w-4 h-4 shrink-0" />
            {label}
          </Link>
        ))}
      </nav>

      <div className="px-3 pb-4">
        <button
          onClick={logout}
          className="flex items-center gap-2.5 px-3 py-2 rounded-md text-sm text-muted-foreground hover:bg-secondary hover:text-foreground w-full transition-colors"
        >
          <LogOut className="w-4 h-4" />
          Sign out
        </button>
      </div>
    </aside>
  );
}
