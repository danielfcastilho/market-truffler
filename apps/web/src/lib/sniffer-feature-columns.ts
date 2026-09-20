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

/** Centralized presentation metadata for every Feature column — the only
 * place header text, grouping, width, and formatting are declared. Order
 * here is also render order; columns must stay grouped consecutively for
 * `groupColumns` to produce one spanning header per family (Returns, RSI,
 * and — with zero changes elsewhere — future families like Volatility or
 * Volume). */
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
    key: "return_1h",
    group: "Returns",
    label: "1h",
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
