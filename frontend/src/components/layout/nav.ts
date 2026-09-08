/**
 * Single source of truth for primary navigation.
 *
 * Sidebar (desktop) and BottomNav (mobile) previously each carried their
 * own hardcoded copy of this list, so adding a page meant remembering to
 * edit both — and the Screener shipped visible on desktop and missing on
 * mobile precisely because that second edit was forgotten. One list
 * removes the whole class of bug.
 *
 * The two surfaces genuinely differ, so those differences are expressed
 * as fields rather than as separate arrays:
 *   shortLabel — the bottom bar is ~50px per item, so some labels need
 *                to be shorter there than in the sidebar.
 *   mobile     — the AI Advisor has a floating action button on mobile,
 *                so it would be redundant in the bottom bar.
 */

import type { LucideIcon } from "lucide-react";
import {
  Bell,
  BookOpen,
  Briefcase,
  CalendarDays,
  Cpu,
  Filter,
  LayoutDashboard,
  LayoutGrid,
  MessageSquare,
  Newspaper,
  TrendingUp,
  Wallet,
} from "lucide-react";

export interface NavItem {
  href: string;
  /** Sidebar label. */
  label: string;
  /** Bottom-bar label when the sidebar one is too long. Defaults to `label`. */
  shortLabel?: string;
  icon: LucideIcon;
  /** Set false to hide from the mobile bottom bar. Defaults to shown. */
  mobile?: boolean;
}

export const NAV_ITEMS: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", shortLabel: "Home", icon: LayoutDashboard },
  { href: "/briefing", label: "Briefing", icon: Newspaper },
  { href: "/portfolio", label: "Portfolio", icon: Briefcase },
  { href: "/accounts", label: "Accounts", icon: Wallet },
  { href: "/watchlist", label: "Watchlist", icon: BookOpen },
  { href: "/signals", label: "Signals", icon: TrendingUp },
  { href: "/strategies", label: "Strategies", shortLabel: "Auto", icon: Cpu },
  { href: "/calendar", label: "Calendar", icon: CalendarDays },
  { href: "/heatmap", label: "Heatmap", icon: LayoutGrid },
  { href: "/screener", label: "Screener", icon: Filter },
  { href: "/alerts", label: "Alerts", icon: Bell },
  // Mobile reaches the advisor through AdvisorFAB, so it would be a
  // duplicate entry in an already-crowded bottom bar.
  { href: "/advisor", label: "AI Advisor", icon: MessageSquare, mobile: false },
];

/** Items shown in the mobile bottom bar. */
export const MOBILE_NAV_ITEMS = NAV_ITEMS.filter((i) => i.mobile !== false);
