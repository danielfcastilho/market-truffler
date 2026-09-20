import type { SnifferFrame } from "@/lib/server-api";
import { FEATURE_COLUMNS, groupColumnsByFamily } from "@/lib/sniffer-feature-columns";
import { SnifferScents } from "@/components/sniffer-scents";
import { cn } from "@/lib/utils";

/**
 * The consolidated microscope for one tracked symbol — Truffle status,
 * Long/Short Scent, the Scents that would explain them, and the factual
 * Sniffs Sniffer currently has. Reuses the exact same frame data and
 * column config (`FEATURE_COLUMNS`) the Sniffs matrix uses, so this
 * page's numbers are always identical to the matrix's — no separate
 * backend endpoint, no duplicated formatting logic. Scent/Overall
 * Scent/Truffle qualification aren't implemented yet, so those rows are
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
          <div className="divide-y divide-border/60 font-mono text-sm">
            {/* Truffle qualification and Overall Scent aren't implemented
                yet — see docs/ARCHITECTURE.md's pipeline. Once a symbol
                can qualify, this is where its 🍄 status belongs. */}
            <StatRow label="Truffle" value="N/A" valueClassName="text-muted-foreground/70" />
            <StatRow label="Long Scent" value="N/A" valueClassName="text-muted-foreground/70" />
            <StatRow label="Short Scent" value="N/A" valueClassName="text-muted-foreground/70" />
          </div>

          <SnifferScents />

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
