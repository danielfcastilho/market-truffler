import { getLatestSnifferFrame } from "@/lib/server-api";
import { SnifferTable } from "@/components/sniffer-table";

function formatFrameTimeUtc(iso: string): string {
  const date = new Date(iso);
  const hh = String(date.getUTCHours()).padStart(2, "0");
  const mm = String(date.getUTCMinutes()).padStart(2, "0");
  return `${hh}:${mm} UTC`;
}

export default async function SnifferPage() {
  const frame = await getLatestSnifferFrame();

  return (
    <div className="mx-auto max-w-2xl px-4 py-10 md:px-8 md:py-14">
      <div className="mb-8 space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">🐽 Sniffer</h1>
        <p className="text-muted-foreground">
          return_5m — the 5-minute close-to-close return of each instrument in the most recently
          analyzed Market Frame. A factual measurement, not a ranking: sorting here only reorders
          the page for you, it never signals a recommendation.
        </p>
      </div>

      {frame === null ? (
        <p className="font-mono text-sm text-muted-foreground">
          No Sniffer analysis yet. Sniffer runs automatically once Market Frames start
          finalizing.
        </p>
      ) : (
        <div className="space-y-4">
          <div className="flex items-center justify-between font-mono text-sm text-muted-foreground">
            <span>Frame {formatFrameTimeUtc(frame.frame_time)}</span>
            <span>{frame.instruments_analyzed} coins analyzed</span>
          </div>
          <SnifferTable instruments={frame.instruments} />
        </div>
      )}
    </div>
  );
}
