import type { SnifferFrame } from "@/lib/server-api";
import { SnifferSymbolSearch } from "@/components/sniffer-symbol-search";
import { SnifferTable } from "@/components/sniffer-table";
import { formatFrameTimeUtc } from "@/lib/sniffer-format";

/**
 * Sniffer's Market Data inspector (`/sniffer/market-data`): every factual,
 * per-instrument measurement (return_5m, rsi_14_5m, ...) for the most
 * recently analyzed Market Frame, plus a jump to any symbol's detail
 * page. Observations only — scores and ranks live on the Rankings tab,
 * and no Truffle is qualified anywhere yet. "Sniff" is the product term for
 * what the backend calls a Feature (`app/features/`, `FeatureEngine`).
 */
export function SnifferSniffs({ frame }: { frame: SnifferFrame | null }) {
  return (
    <div className="space-y-4">
      {frame === null ? (
        <p className="font-mono text-sm text-muted-foreground">
          No Sniffer analysis yet. Sniffer runs automatically once Market Frames start finalizing.
        </p>
      ) : (
        <div className="flex items-center justify-between font-mono text-sm text-muted-foreground">
          <span>Frame {formatFrameTimeUtc(frame.frame_time)}</span>
          <span>{frame.instruments_analyzed} coins analyzed</span>
        </div>
      )}
      <div className="max-w-2xl">
        <SnifferSymbolSearch />
      </div>
      {frame !== null && <SnifferTable instruments={frame.instruments} />}
    </div>
  );
}
