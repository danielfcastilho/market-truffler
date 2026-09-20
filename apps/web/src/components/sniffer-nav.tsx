"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

/**
 * Which top-level Sniffer tab is active — pure route matching, no query
 * params. `/sniffer` itself is the 🍄 Truffles dashboard (there is no
 * separate "Dashboard" view — Truffles *is* the landing page); Sniffs has
 * its own route. `null` on `/sniffer/{symbol}` (a dynamic page that isn't
 * one of these two tabs) — the nav still renders there via the shared
 * layout, just with nothing highlighted.
 */
export type SnifferTab = "truffles" | "sniffs";

const TABS: { tab: SnifferTab; label: string; href: string }[] = [
  { tab: "truffles", label: "🍄 Truffles", href: "/sniffer" },
  { tab: "sniffs", label: "Sniffs", href: "/sniffer/sniffs" },
];

function useActiveTab(): SnifferTab | null {
  const pathname = usePathname();
  if (pathname === "/sniffer") return "truffles";
  if (pathname === "/sniffer/sniffs") return "sniffs";
  return null;
}

export function SnifferNav() {
  const active = useActiveTab();
  return (
    <nav aria-label="Sniffer view" className="mb-8 flex gap-2 font-mono text-sm">
      {TABS.map((tab) => (
        <Link
          key={tab.tab}
          href={tab.href}
          aria-current={active === tab.tab ? "page" : undefined}
          className={cn(
            "rounded-md px-3 py-1.5 font-medium transition-colors",
            active === tab.tab
              ? "bg-accent text-accent-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {tab.label}
        </Link>
      ))}
    </nav>
  );
}
