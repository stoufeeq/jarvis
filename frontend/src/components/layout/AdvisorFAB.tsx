"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { MessageSquare } from "lucide-react";

export function AdvisorFAB() {
  const pathname = usePathname();
  // Hide on the advisor page itself — no point overlapping the chat UI
  if (pathname.startsWith("/advisor")) return null;

  return (
    <Link
      href="/advisor"
      className="md:hidden fixed z-50 flex items-center justify-center w-14 h-14 rounded-full shadow-lg bg-primary/90 text-primary-foreground hover:bg-primary transition-transform active:scale-95"
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
