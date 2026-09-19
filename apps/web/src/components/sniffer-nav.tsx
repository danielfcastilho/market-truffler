import Link from "next/link";
import type { SnifferView } from "@/lib/sniffer-view";
import { cn } from "@/lib/utils";

const TABS: { view: SnifferView; label: string; href: string }[] = [
  { view: "truffles", label: "🍄 Truffles", href: "/sniffer" },
  { view: "notes", label: "Notes", href: "/sniffer?view=notes" },
  { view: "features", label: "Features", href: "/sniffer?view=features" },
];

export function SnifferNav({ view }: { view: SnifferView }) {
  return (
    <nav aria-label="Sniffer view" className="mb-8 flex gap-2 font-mono text-sm">
      {TABS.map((tab) => (
        <Link
          key={tab.view}
          href={tab.href}
          aria-current={view === tab.view ? "page" : undefined}
          className={cn(
            "rounded-md px-3 py-1.5 font-medium transition-colors",
            view === tab.view
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
