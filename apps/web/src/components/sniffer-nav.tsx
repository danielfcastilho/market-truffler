"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const SECTIONS = [
  { href: "/sniffer", label: "🍄 Rankings" },
  { href: "/sniffer/market-data", label: "📊 Market Data" },
];

/** Sniffer's sections. A symbol page belongs to neither tab. */
export function SnifferNav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Sniffer sections" className="mb-8 flex flex-wrap gap-1 border-b border-border">
      {SECTIONS.map((s) => {
        const active = pathname === s.href;
        return (
          <Link
            key={s.href}
            href={s.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-sm transition-colors",
              active
                ? "border-primary font-medium text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {s.label}
          </Link>
        );
      })}
    </nav>
  );
}
