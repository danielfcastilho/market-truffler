import type { SnifferFrame } from "@/lib/server-api";
import { FEATURE_COLUMNS, groupColumnsByFamily } from "@/lib/sniffer-feature-columns";
import { cn } from "@/lib/utils";

/**
 * Market Data detail for one tracked symbol: every Sniff (factual
 * measurement) Sniffer computed for it in the latest analyzed frame,
 * grouped like the matrix. Reuses the exact same frame data and column
 * config (`FEATURE_COLUMNS`) the Sniffs matrix uses, so its numbers are
 * always identical to the matrix's — no separate backend endpoint, no
 * duplicated formatting logic. Observations only: Long/Short Score, Rank,
 * and Truffle qualification belong to OINK CORP's future ranking runtime,
 * not to Sniffer, so none of them appear here.
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
