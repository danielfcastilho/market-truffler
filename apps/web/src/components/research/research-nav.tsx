"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

export const RESEARCH_SECTIONS = [
  { href: "/oink-corp", label: "Overview" },
  { href: "/oink-corp/models", label: "Models" },
  { href: "/oink-corp/hypotheses", label: "Hypotheses" },
  { href: "/oink-corp/data-health", label: "Data readiness" },
];

/** Which section a research path belongs to. Campaign and experiment pages
 * are hypothesis detail; the retired ranking page now lives under Models. */
export function activeResearchSection(pathname: string): string | null {
  if (pathname === "/oink-corp") return "/oink-corp";
  if (
    pathname.startsWith("/oink-corp/hypotheses") ||
    pathname.startsWith("/oink-corp/campaigns") ||
    pathname.startsWith("/oink-corp/experiments")
  ) {
    return "/oink-corp/hypotheses";
  }
  if (pathname.startsWith("/oink-corp/models") || pathname.startsWith("/oink-corp/ranking")) {
    return "/oink-corp/models";
  }
  if (pathname.startsWith("/oink-corp/data-health")) return "/oink-corp/data-health";
  return null;
}

/** OINK CORP's research sections, plus the audit log as a secondary link. */
export function ResearchNav() {
  const pathname = usePathname();
  const active = activeResearchSection(pathname);
  const onHistory = pathname.startsWith("/oink-corp/history");
  return (
    <nav
      aria-label="Research sections"
      className="mb-8 flex flex-wrap items-end justify-between gap-2 border-b border-border"
    >
      <div className="flex flex-wrap gap-1">
        {RESEARCH_SECTIONS.map((s) => (
          <Link
            key={s.href}
            href={s.href}
            aria-current={active === s.href ? "page" : undefined}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-sm transition-colors",
              active === s.href
                ? "border-primary font-medium text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {s.label}
          </Link>
        ))}
      </div>
      <Link
        href="/oink-corp/history"
        aria-current={onHistory ? "page" : undefined}
        className={cn(
          "px-3 py-2 text-xs underline-offset-4 hover:underline",
          onHistory ? "font-medium text-foreground" : "text-muted-foreground",
        )}
      >
        Audit log
      </Link>
    </nav>
  );
}
