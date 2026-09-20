import type { SnifferFrame } from "@/lib/server-api";
import { SnifferTable } from "@/components/sniffer-table";
import { formatFrameTimeUtc } from "@/lib/sniffer-format";

/**
 * The Sniffs matrix: Sniffer's factual, per-instrument measurements
 * (return_5m, return_1h, rsi_14_5m, ...) for the most recently analyzed
 * Market Frame. A secondary inspection/research surface, not the
 * operational 🍄 Truffles dashboard (`/sniffer` itself, see
 * SnifferDashboard) — see docs/ARCHITECTURE.md for the Sniffs → Scents →
 * Score → Rank → Truffles pipeline. "Sniff" is the product term for what
 * the backend calls a Feature (`app/features/`, `FeatureEngine`) — those
 * internal names are unchanged; only the UI label is renamed.
 */
export function SnifferSniffs({ frame }: { frame: SnifferFrame | null }) {
  if (frame === null) {
    return (
      <p className="font-mono text-sm text-muted-foreground">
        No Sniffer analysis yet. Sniffer runs automatically once Market Frames start finalizing.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between font-mono text-sm text-muted-foreground">
        <span>Frame {formatFrameTimeUtc(frame.frame_time)}</span>
        <span>{frame.instruments_analyzed} coins analyzed</span>
      </div>
      <SnifferTable instruments={frame.instruments} />
    </div>
  );
}
