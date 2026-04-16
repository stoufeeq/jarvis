"use client";

import { useEffect, useState, useRef } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { BottomNav } from "@/components/layout/BottomNav";
import { AdvisorFAB } from "@/components/layout/AdvisorFAB";
import { useAuthStore } from "@/store/auth";
import { useQueryClient } from "@tanstack/react-query";
import { alertsApi } from "@/lib/api";
import type { Alert } from "@/types";
import toast from "react-hot-toast";

const ALERT_POLL_MS = 60_000; // check every 60 seconds

function useAlertPoller(enabled: boolean) {
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const qc = useQueryClient();

  async function check() {
    try {
      const res = await alertsApi.check();
      const triggered: Alert[] = res.data;
      if (triggered.length > 0) {
        // Invalidate so the bell badge updates immediately
        qc.invalidateQueries({ queryKey: ["alerts"] });
        triggered.forEach((a) => {
          const label =
            a.alert_type === "price_above"
              ? `above $${a.threshold_value}`
              : `below $${a.threshold_value}`;
          toast(`🔔 ${a.ticker} ${label}`, {
            duration: 8000,
            style: {
              background: "#1e293b",
              color: "#f1f5f9",
              border: "1px solid #f59e0b",
            },
          });
        });
      }
    } catch {
      // silently ignore — user may not be logged in yet
    }
  }

  useEffect(() => {
    if (!enabled) return;
    check();
    timerRef.current = setInterval(check, ALERT_POLL_MS);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [enabled]); // eslint-disable-line react-hooks/exhaustive-deps
}

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const accessToken = useAuthStore((s) => s.accessToken);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    const unsub = useAuthStore.persist.onFinishHydration(() => setHydrated(true));
    if (useAuthStore.persist.hasHydrated()) setHydrated(true);
    return unsub;
  }, []);

  useEffect(() => {
    if (hydrated && !accessToken) {
      router.replace("/login");
    }
  }, [hydrated, accessToken, router]);

  // Only poll once fully hydrated and authenticated
  useAlertPoller(hydrated && !!accessToken);

  if (!hydrated) return null;

  return (
    <div className="flex overflow-hidden" style={{ height: "100dvh" }}>
      {/* Sidebar — desktop only */}
      <div className="hidden md:flex">
        <Sidebar />
      </div>
      <div className="flex flex-col flex-1 overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-4 md:p-6 pb-36 md:pb-6 safe-bottom">
          {children}
        </main>
      </div>
      {/* Bottom nav — mobile only */}
      <BottomNav />
      {/* AI Advisor FAB — mobile only, floats above bottom nav */}
      <AdvisorFAB />
    </div>
  );
}
