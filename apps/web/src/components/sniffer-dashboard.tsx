"use client";

import { useState } from "react";
import type { SnifferFrame } from "@/lib/server-api";
import { formatFrameTimeUtc } from "@/lib/sniffer-format";
import { SnifferSymbolSearch } from "@/components/sniffer-symbol-search";
import { TrufflePanel } from "@/components/truffle-panel";
import {
  TruffleDirectionTabs,
  truffleTabId,
  trufflePanelId,
} from "@/components/truffle-direction-tabs";
import type { TruffleDirection } from "@/components/truffle-icon";

/**
 * `/sniffer` itself — the 🍄 Truffles dashboard, Sniffer's primary page.
 * There is no separate "Dashboard" view: Truffles *are* the dashboard's
 * main content, not a different product. Frame context (when/how much)
 * and a way to jump straight to any tracked symbol are shared above the
 * Green/Red Truffle sub-tabs; only one directional ranking is shown at a
 * time (defaulting to Green — a UI default only, not a claim that Long is
 * mathematically preferred), so it can use the full content width instead
 * of splitting it with a sibling table. Sniffer has no Scent/Score/Rank/
 * qualification logic yet, so both directions render the exact same
 * truthful empty state — never a fabricated ranking. Deliberately has no
 * awareness of Warhog/trades/positions (see docs/ARCHITECTURE.md) — this
 * only ever reflects what Sniffer itself has observed and measured.
 */
export function SnifferDashboard({ frame }: { frame: SnifferFrame | null }) {
  const [direction, setDirection] = useState<TruffleDirection>("long");

  return (
    <div className="max-w-2xl space-y-6">
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

      <div className="space-y-4">
        <TruffleDirectionTabs active={direction} onChange={setDirection} />
        <div
          role="tabpanel"
          id={trufflePanelId(direction)}
          aria-labelledby={truffleTabId(direction)}
        >
          <TrufflePanel direction={direction} />
        </div>
      </div>
    </div>
  );
}
