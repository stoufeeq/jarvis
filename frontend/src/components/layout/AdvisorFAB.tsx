"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { MessageSquare } from "lucide-react";
import { cn } from "@/lib/utils";

export function AdvisorFAB() {
  const pathname = usePathname();
  const active = pathname.startsWith("/advisor");

  return (
    <Link
      href="/advisor"
      className={cn(
        // Only visible on mobile, sits above the bottom nav
        "md:hidden fixed z-50 flex items-center justify-center",
        "w-14 h-14 rounded-full shadow-lg transition-transform active:scale-95",
        active
          ? "bg-primary text-primary-foreground"
          : "bg-primary/90 text-primary-foreground hover:bg-primary"
      )}
      style={{
        bottom: "calc(4rem + env(safe-area-inset-bottom))",
        right: "1rem",
      }}
      title="AI Advisor"
    >
      <MessageSquare className="w-6 h-6" />
    </Link>
  );
}
