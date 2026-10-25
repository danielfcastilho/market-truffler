"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const SECTIONS = [
  { href: "/oink-corp", label: "Feature research" },
  { href: "/oink-corp/ranking", label: "Ranking research" },
  { href: "/oink-corp/models", label: "Model knowledge" },
  { href: "/oink-corp/data-health", label: "Data health" },
  { href: "/oink-corp/history", label: "History" },
];

/** OINK CORP's research sections. Campaign and experiment pages belong to feature research. */
export function ResearchNav() {
  const pathname = usePathname();
  const active = (href: string) =>
    href === "/oink-corp"
      ? pathname === href ||
        pathname.startsWith("/oink-corp/campaigns") ||
        pathname.startsWith("/oink-corp/experiments")
      : pathname.startsWith(href);
  return (
    <nav aria-label="Research sections" className="mb-8 flex flex-wrap gap-1 border-b border-border">
      {SECTIONS.map((s) => (
        <Link
          key={s.href}
          href={s.href}
          aria-current={active(s.href) ? "page" : undefined}
          className={cn(
            "-mb-px border-b-2 px-3 py-2 text-sm transition-colors",
            active(s.href)
              ? "border-primary font-medium text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground",
          )}
        >
          {s.label}
        </Link>
      ))}
    </nav>
  );
}
