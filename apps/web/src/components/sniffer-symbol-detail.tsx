import type { SnifferFrame } from "@/lib/server-api";
import { FEATURE_COLUMNS, groupColumnsByFamily } from "@/lib/sniffer-feature-columns";
import { SnifferScents } from "@/components/sniffer-scents";
import { cn } from "@/lib/utils";

/**
 * The consolidated microscope for one tracked symbol, laid out in the
 * same order the pipeline flows — facts first, conclusions last: Sniffs
 * (factual measurements), Scents (the interpreted dimensions that would
 * explain a Score), Score (Long/Short directional desirability), Rank
 * (this symbol's position within the analyzed universe for that
 * direction), and finally Truffle status. Reuses the exact same frame
 * data and column config (`FEATURE_COLUMNS`) the Sniffs matrix uses, so
 * this page's Sniffs numbers are always identical to the matrix's — no
 * separate backend endpoint, no duplicated formatting logic. Scent/Score/
 * Rank/Truffle qualification aren't implemented yet, so those rows are
 * always "N/A" here — never fabricated, never 0 (0 will eventually be a
 * real, meaningful score).
 */
export function SnifferSymbolDetail({
  symbol,
  frame,
}: {
  symbol: string;
  frame: SnifferFrame | null;
}) {
  const instrument =
    frame?.instruments.find((i) => i.symbol.toUpperCase() === symbol.toUpperCase()) ?? null;

  return (
    <div className="max-w-2xl space-y-8">
      <h2 className="text-xl font-semibold tracking-tight">{symbol}</h2>

      {instrument === null ? (
        <p className="font-mono text-sm text-muted-foreground">
          {frame === null
            ? "No Sniffer analysis yet. Sniffer runs automatically once Market Frames start finalizing."
            : `No Sniffer data for ${symbol}. It may not be a tracked instrument, or hasn't been analyzed yet.`}
        </p>
      ) : (
        <>
          <div className="space-y-4">
            <h3 className="font-mono text-sm font-medium text-foreground">Sniffs</h3>
            {groupColumnsByFamily(FEATURE_COLUMNS).map((group) => (
              <div key={group.name} className="space-y-1">
                <h4 className="font-mono text-xs font-medium tracking-wide text-muted-foreground uppercase">
                  {group.name}
                </h4>
                <div className="divide-y divide-border/60 font-mono text-sm">
                  {group.columns.map((column) => (
                    <StatRow
                      key={column.key}
                      label={column.label}
                      title={column.key}
                      value={column.format(instrument[column.key])}
                      valueClassName={column.valueClassName(instrument[column.key])}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>

          <SnifferScents />

          {/* Score, Rank, and Truffle qualification aren't implemented
              yet — see docs/ARCHITECTURE.md's pipeline. Once a symbol can
              be scored/ranked/qualified, this is where its 🍄 status
              belongs. "Score" (not "Scent") is deliberate: Scent is
              reserved for the intermediate Pillar-level dimensions above,
              not this final aggregate. */}
          <div className="space-y-2">
            <h3 className="font-mono text-sm font-medium text-foreground">Score</h3>
            <div className="divide-y divide-border/60 font-mono text-sm">
              <StatRow label="Long" value="N/A" valueClassName="text-muted-foreground/70" />
              <StatRow label="Short" value="N/A" valueClassName="text-muted-foreground/70" />
            </div>
          </div>

          <div className="space-y-2">
            <h3 className="font-mono text-sm font-medium text-foreground">Rank</h3>
            <div className="divide-y divide-border/60 font-mono text-sm">
              <StatRow label="Long" value="N/A" valueClassName="text-muted-foreground/70" />
              <StatRow label="Short" value="N/A" valueClassName="text-muted-foreground/70" />
            </div>
          </div>

          <div className="font-mono text-sm">
            <StatRow label="🍄 Truffle" value="N/A" valueClassName="text-muted-foreground/70" />
          </div>
        </>
      )}
    </div>
  );
}

function StatRow({
  label,
  value,
  title,
  valueClassName,
}: {
  label: string;
  value: string;
  title?: string;
  valueClassName?: string;
}) {
  return (
    <div className="flex items-center justify-between py-2">
      <span className="text-muted-foreground" title={title}>
        {label}
      </span>
      <span className={cn("font-medium", valueClassName)}>{value}</span>
    </div>
  );
}
