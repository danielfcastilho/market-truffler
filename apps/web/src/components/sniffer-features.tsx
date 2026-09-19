import type { SnifferFrame } from "@/lib/server-api";
import { SnifferTable } from "@/components/sniffer-table";

function formatFrameTimeUtc(iso: string): string {
  const date = new Date(iso);
  const hh = String(date.getUTCHours()).padStart(2, "0");
  const mm = String(date.getUTCMinutes()).padStart(2, "0");
  return `${hh}:${mm} UTC`;
}

/**
 * The feature matrix: Sniffer's factual, per-instrument measurements
 * (return_5m, return_1h) for the most recently analyzed Market Frame. A
 * secondary inspection/research surface, not the operational 🍄 Truffles
 * view (see SnifferTruffles) — see docs/ARCHITECTURE.md for the Features
 * vs. Truffles distinction.
 */
export function SnifferFeatures({ frame }: { frame: SnifferFrame | null }) {
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
