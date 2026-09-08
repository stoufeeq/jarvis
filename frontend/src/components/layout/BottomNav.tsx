"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";
import { MOBILE_NAV_ITEMS } from "./nav";



export function BottomNav() {
  const pathname = usePathname();

  return (
    <nav className="md:hidden fixed bottom-0 inset-x-0 z-40 flex border-t border-border bg-card overflow-x-auto" style={{ paddingBottom: "env(safe-area-inset-bottom)" }}>
      {MOBILE_NAV_ITEMS.map(({ href, label, shortLabel, icon: Icon }) => {
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
            {shortLabel ?? label}
          </Link>
        );
      })}
    </nav>
  );
}
