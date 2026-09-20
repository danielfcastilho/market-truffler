import type { SnifferFrame } from "@/lib/server-api";
import { formatFrameTimeUtc } from "@/lib/sniffer-format";
import { SnifferSymbolSearch } from "@/components/sniffer-symbol-search";
import { TrufflePanel } from "@/components/truffle-panel";

/**
 * `/sniffer` itself — the 🍄 Truffles dashboard, Sniffer's primary page.
 * There is no separate "Dashboard" view: Truffles *are* the dashboard's
 * main content, not a different product. Frame context (when/how much),
 * a way to jump straight to any tracked symbol, and each direction's top
 * 🍄 Truffles side by side. Sniffer has no Scent/ranking/qualification
 * logic yet, so both panels are the exact same truthful empty state —
 * never a fabricated top 5. `TrufflePanel` takes no `limit` today (there's
 * nothing to truncate yet) but is written generically enough to grow past
 * five entries once real ranking exists. Deliberately has no awareness of
 * Warhog/trades/positions (see docs/ARCHITECTURE.md) — this only ever
 * reflects what Sniffer itself has observed and measured.
 */
export function SnifferDashboard({ frame }: { frame: SnifferFrame | null }) {
  return (
    <div className="max-w-2xl space-y-8">
      <div className="space-y-4">
        <div className="flex items-center justify-between font-mono text-sm text-muted-foreground">
          {frame === null ? (
            <span>No Sniffer analysis yet</span>
          ) : (
            <>
              <span>Frame {formatFrameTimeUtc(frame.frame_time)}</span>
              <span>{frame.instruments_analyzed} coins analyzed</span>
            </>
          )}
        </div>
        <SnifferSymbolSearch />
      </div>

      <div className="grid gap-8 md:grid-cols-2">
        <TrufflePanel label="Top Long Truffles" />
        <TrufflePanel label="Top Short Truffles" />
      </div>
    </div>
  );
}
