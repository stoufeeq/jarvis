"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut } from "lucide-react";
import { cn } from "@/lib/utils";
import { NAV_ITEMS } from "./nav";
import { useAuthStore } from "@/store/auth";
import { useQueryClient } from "@tanstack/react-query";



export function Sidebar() {
  const pathname = usePathname();
  const logout = useAuthStore((s) => s.logout);
  const qc = useQueryClient();

  const handleLogout = () => {
    logout();
    qc.clear();
  };

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
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => (
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
          onClick={handleLogout}
          className="flex items-center gap-2.5 px-3 py-2 rounded-md text-sm text-muted-foreground hover:bg-secondary hover:text-foreground w-full transition-colors"
        >
          <LogOut className="w-4 h-4" />
          Sign out
        </button>
      </div>
    </aside>
  );
}
