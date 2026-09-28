import type { SnifferInstrument } from "@/lib/server-api";

/** Any Feature key the Sniffs matrix can show a column for — everything on
 * `SnifferInstrument` except the identity fields. Adding a new backend
 * Feature only ever means adding one more entry to `FEATURE_COLUMNS`
 * below; nothing about the table's structure (grouping, sticky columns,
 * sorting, filtering) depends on which or how many keys exist. */
export type FeatureKey = Exclude<keyof SnifferInstrument, "instrument_id" | "symbol">;

export interface FeatureColumn {
  /** The canonical backend Feature name — never renamed. Doubles as the
   * header's `title` attribute (a native tooltip), so the machine name
   * stays one hover away even though the visible header shows `label`. */
  key: FeatureKey;
  /** Family this column belongs to; consecutive columns sharing a group
   * are rendered under one spanning header cell (see `groupColumns`). */
  group: string;
  /** Compact, human-readable leaf header shown in the table. */
  label: string;
  /** Sensible minimum column width in pixels, so labels/values never
   * collide regardless of how narrow the viewport is. */
  minWidthPx: number;
  format: (value: string | null) => string;
  valueClassName: (value: string | null) => string;
}

function formatReturn(value: string | null): string {
  if (value == null) return "N/A";
  const percent = Number(value) * 100;
  const sign = percent > 0 ? "+" : "";
  return `${sign}${percent.toFixed(2)}%`;
}

function returnClassName(value: string | null): string {
  if (value == null) return "text-muted-foreground/70";
  const numeric = Number(value);
  if (numeric > 0) return "text-primary";
  if (numeric < 0) return "text-destructive";
  return "text-foreground";
}

// A plain number (e.g. "63.42"), never a percentage — RSI is already a
// 0-100 index, not a fraction to be multiplied.
function formatRsi(value: string | null): string {
  if (value == null) return "N/A";
  return Number(value).toFixed(2);
}

// RSI is a factual measurement, not a signal — deliberately no green/red or
// any other value-based styling (no "bullish"/"bearish"/"overbought"/
// "oversold" framing). The only distinction made here is real vs.
// unavailable, exactly like every other feature column.
function rsiClassName(value: string | null): string {
  return value == null ? "text-muted-foreground/70" : "text-foreground";
}

// Volatility has no direction (it's ATR% — always non-negative), so unlike
// formatReturn this never shows a +/- sign — just a plain percentage.
function formatVolatility(value: string | null): string {
  if (value == null) return "N/A";
  const percent = Number(value) * 100;
  return `${percent.toFixed(2)}%`;
}

// No green/red or any other value-based styling, for the same reason as
// RSI: this is a raw descriptive measurement, not a signal, and having no
// sign means "high vs low" framing would be an invented judgment this
// column doesn't make. Real vs. unavailable is the only distinction.
function volatilityClassName(value: string | null): string {
  return value == null ? "text-muted-foreground/70" : "text-foreground";
}

// A plain multiple (e.g. "2.4x"), never a percentage — RVOL is already a
// ratio to the instrument's own normal volume, not a fraction to be
// multiplied by 100. Unsigned, like Volatility: no green/red styling,
// since "high vs low" participation is a descriptive fact, not a signal.
function formatRelativeVolume(value: string | null): string {
  if (value == null) return "N/A";
  return `${Number(value).toFixed(1)}x`;
}

function relativeVolumeClassName(value: string | null): string {
  return value == null ? "text-muted-foreground/70" : "text-foreground";
}

// Funding rates are tiny relative to Returns/OI Δ (typically hundredths
// of a percent), so this keeps 4 decimal places rather than Return's 2 —
// otherwise most real readings would round to "0.00%" and lose all
// signal. Signed and colored exactly like Returns/OI Δ: funding pressure
// has a real, meaningful direction (positive == longs pay shorts).
function formatFundingRate(value: string | null): string {
  if (value == null) return "N/A";
  const percent = Number(value) * 100;
  const sign = percent > 0 ? "+" : "";
  return `${sign}${percent.toFixed(4)}%`;
}

/** Centralized presentation metadata for every Feature column — the only
 * place header text, grouping, width, and formatting are declared. Order
 * here is also render order; columns must stay grouped consecutively for
 * `groupColumns` to produce one spanning header per family (Returns, RSI,
 * OI Δ, Volatility, Relative Volume, Funding Rate, and — with zero
 * changes elsewhere — whatever family comes next). */
export const FEATURE_COLUMNS: FeatureColumn[] = [
  {
    key: "return_5m",
    group: "Returns",
    label: "5m",
    minWidthPx: 84,
    format: formatReturn,
    valueClassName: returnClassName,
  },
  {
    key: "return_15m",
    group: "Returns",
    label: "15m",
    minWidthPx: 84,
    format: formatReturn,
    valueClassName: returnClassName,
  },
  {
    key: "return_1h",
    group: "Returns",
    label: "1h",
    minWidthPx: 84,
    format: formatReturn,
    valueClassName: returnClassName,
  },
  {
    key: "return_4h",
    group: "Returns",
    label: "4h",
    minWidthPx: 84,
    format: formatReturn,
    valueClassName: returnClassName,
  },
  {
    key: "return_24h",
    group: "Returns",
    label: "24h",
    minWidthPx: 84,
    format: formatReturn,
    valueClassName: returnClassName,
  },
  {
    key: "rsi_14_5m",
    group: "RSI",
    label: "5m",
    minWidthPx: 76,
    format: formatRsi,
    valueClassName: rsiClassName,
  },
  {
    key: "rsi_14_15m",
    group: "RSI",
    label: "15m",
    minWidthPx: 76,
    format: formatRsi,
    valueClassName: rsiClassName,
  },
  {
    key: "rsi_14_1h",
    group: "RSI",
    label: "1h",
    minWidthPx: 76,
    format: formatRsi,
    valueClassName: rsiClassName,
  },
  {
    key: "rsi_14_4h",
    group: "RSI",
    label: "4h",
    minWidthPx: 76,
    format: formatRsi,
    valueClassName: rsiClassName,
  },
  {
    // Open Interest change reuses Returns' own formatter/coloring: it's
    // the same signed decimal-fraction convention (see SnifferInstrument
    // in server-api.ts), just sourced from OI observations instead of
    // candle closes — no separate "OI" formatting vocabulary needed.
    key: "oi_change_5m",
    group: "OI Δ",
    label: "5m",
    minWidthPx: 84,
    format: formatReturn,
    valueClassName: returnClassName,
  },
  {
    key: "oi_change_15m",
    group: "OI Δ",
    label: "15m",
    minWidthPx: 84,
    format: formatReturn,
    valueClassName: returnClassName,
  },
  {
    key: "oi_change_1h",
    group: "OI Δ",
    label: "1h",
    minWidthPx: 84,
    format: formatReturn,
    valueClassName: returnClassName,
  },
  {
    key: "oi_change_4h",
    group: "OI Δ",
    label: "4h",
    minWidthPx: 84,
    format: formatReturn,
    valueClassName: returnClassName,
  },
  {
    key: "oi_change_24h",
    group: "OI Δ",
    label: "24h",
    minWidthPx: 84,
    format: formatReturn,
    valueClassName: returnClassName,
  },
  {
    key: "volatility_15m",
    group: "Volatility",
    label: "15m",
    minWidthPx: 84,
    format: formatVolatility,
    valueClassName: volatilityClassName,
  },
  {
    key: "volatility_1h",
    group: "Volatility",
    label: "1h",
    minWidthPx: 84,
    format: formatVolatility,
    valueClassName: volatilityClassName,
  },
  {
    key: "volatility_4h",
    group: "Volatility",
    label: "4h",
    minWidthPx: 84,
    format: formatVolatility,
    valueClassName: volatilityClassName,
  },
  {
    key: "volatility_24h",
    group: "Volatility",
    label: "24h",
    minWidthPx: 84,
    format: formatVolatility,
    valueClassName: volatilityClassName,
  },
  {
    key: "relative_volume_15m",
    group: "Relative Volume",
    label: "15m",
    minWidthPx: 76,
    format: formatRelativeVolume,
    valueClassName: relativeVolumeClassName,
  },
  {
    key: "relative_volume_1h",
    group: "Relative Volume",
    label: "1h",
    minWidthPx: 76,
    format: formatRelativeVolume,
    valueClassName: relativeVolumeClassName,
  },
  {
    key: "relative_volume_4h",
    group: "Relative Volume",
    label: "4h",
    minWidthPx: 76,
    format: formatRelativeVolume,
    valueClassName: relativeVolumeClassName,
  },
  {
    key: "funding_rate_current",
    group: "Funding Rate",
    label: "Current",
    minWidthPx: 96,
    format: formatFundingRate,
    valueClassName: returnClassName,
  },
  {
    key: "funding_rate_24h_avg",
    group: "Funding Rate",
    label: "24h Avg",
    minWidthPx: 96,
    format: formatFundingRate,
    valueClassName: returnClassName,
  },
];

export interface ColumnGroup<T extends { group: string }> {
  name: string;
  columns: T[];
}

/** Collapses a column list into consecutive same-`group` runs, keeping
 * each run's full column objects — generic over any column list, so it
 * never needs updating as families are added or reordered. Used by the
 * Sniffs matrix (via `groupColumns`, below, for header colSpans) and by
 * the symbol detail page (which needs each group's actual columns, not
 * just a count, to render a "Returns" / "RSI" section list). */
export function groupColumnsByFamily<T extends { group: string }>(columns: T[]): ColumnGroup<T>[] {
  const runs: ColumnGroup<T>[] = [];
  for (const column of columns) {
    const last = runs[runs.length - 1];
    if (last && last.name === column.group) {
      last.columns.push(column);
    } else {
      runs.push({ name: column.group, columns: [column] });
    }
  }
  return runs;
}

export interface ColumnGroupRun {
  name: string;
  /** How many consecutive columns this group's header cell should span. */
  span: number;
}

/** Same grouping as `groupColumnsByFamily`, reduced to just a span count
 * — all the Sniffs matrix header needs for its `colSpan` cells. */
export function groupColumns(columns: { group: string }[]): ColumnGroupRun[] {
  return groupColumnsByFamily(columns).map((group) => ({
    name: group.name,
    span: group.columns.length,
  }));
}
