"use client";

import { useMemo, useState } from "react";
import type { SnifferInstrument } from "@/lib/server-api";
import { cn } from "@/lib/utils";

type ReturnColumn = "return_5m" | "return_1h";
type RsiColumn = "rsi_14_5m" | "rsi_14_15m" | "rsi_14_1h" | "rsi_14_4h";
type SortColumn = "symbol" | ReturnColumn | RsiColumn;
type SortDirection = "asc" | "desc";

const RETURN_COLUMNS: { key: ReturnColumn; label: string }[] = [
  { key: "return_5m", label: "return_5m" },
  { key: "return_1h", label: "return_1h" },
];

// Same canonical order as the underlying MARKET timeframes: 5m, 15m, 1h, 4h.
const RSI_COLUMNS: { key: RsiColumn; label: string }[] = [
  { key: "rsi_14_5m", label: "rsi_14_5m" },
  { key: "rsi_14_15m", label: "rsi_14_15m" },
  { key: "rsi_14_1h", label: "rsi_14_1h" },
  { key: "rsi_14_4h", label: "rsi_14_4h" },
];

/**
 * Purely client-side, viewer-local sorting. This is not a ranking feature:
 * the default is alphabetical by symbol (neutral, implies nothing about
 * desirability), and toggling any column only reorders what's already on
 * the page for the person looking at it — it never calls the backend,
 * which has no ranking/sort concept of its own.
 */
export function SnifferTable({ instruments }: { instruments: SnifferInstrument[] }) {
  const [sortColumn, setSortColumn] = useState<SortColumn>("symbol");
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");

  const sorted = useMemo(() => {
    const rows = [...instruments];
    rows.sort((a, b) => {
      let comparison: number;
      if (sortColumn === "symbol") {
        comparison = a.symbol.localeCompare(b.symbol);
      } else {
        const aValue = a[sortColumn] != null ? Number(a[sortColumn]) : null;
        const bValue = b[sortColumn] != null ? Number(b[sortColumn]) : null;
        if (aValue == null && bValue == null) return 0;
        if (aValue == null) return 1; // unavailable stays last in either direction
        if (bValue == null) return -1;
        comparison = aValue - bValue;
      }
      return sortDirection === "asc" ? comparison : -comparison;
    });
    return rows;
  }, [instruments, sortColumn, sortDirection]);

  function toggleSort(column: SortColumn) {
    if (sortColumn === column) {
      setSortDirection((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortColumn(column);
      setSortDirection("asc");
    }
  }

  return (
    <table className="w-full font-mono text-sm">
      <thead>
        <tr className="border-b border-dashed border-border text-left text-muted-foreground">
          <SortableHeader
            label="Symbol"
            active={sortColumn === "symbol"}
            direction={sortDirection}
            onClick={() => toggleSort("symbol")}
          />
          {RETURN_COLUMNS.map((column) => (
            <SortableHeader
              key={column.key}
              label={column.label}
              active={sortColumn === column.key}
              direction={sortDirection}
              onClick={() => toggleSort(column.key)}
              align="right"
            />
          ))}
          {RSI_COLUMNS.map((column) => (
            <SortableHeader
              key={column.key}
              label={column.label}
              active={sortColumn === column.key}
              direction={sortDirection}
              onClick={() => toggleSort(column.key)}
              align="right"
            />
          ))}
        </tr>
      </thead>
      <tbody className="divide-y divide-border/60">
        {sorted.map((instrument) => (
          <tr key={instrument.instrument_id}>
            <td className="py-2 pr-4 font-medium">{instrument.symbol}</td>
            {RETURN_COLUMNS.map((column) => (
              <td
                key={column.key}
                className={cn("py-2 text-right", returnClass(instrument[column.key]))}
              >
                {formatReturn(instrument[column.key])}
              </td>
            ))}
            {RSI_COLUMNS.map((column) => (
              <td
                key={column.key}
                className={cn("py-2 text-right", rsiClass(instrument[column.key]))}
              >
                {formatRsi(instrument[column.key])}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function SortableHeader({
  label,
  active,
  direction,
  onClick,
  align = "left",
}: {
  label: string;
  active: boolean;
  direction: SortDirection;
  onClick: () => void;
  align?: "left" | "right";
}) {
  return (
    <th className={cn("pb-2 font-medium", align === "right" && "text-right")}>
      <button
        type="button"
        onClick={onClick}
        className={cn(
          "inline-flex items-center gap-1 hover:text-foreground",
          active && "text-foreground",
        )}
      >
        {label}
        {active && <span className="text-xs">{direction === "asc" ? "▲" : "▼"}</span>}
      </button>
    </th>
  );
}

function returnClass(value: string | null): string {
  if (value == null) return "text-muted-foreground/70";
  const numeric = Number(value);
  if (numeric > 0) return "text-primary";
  if (numeric < 0) return "text-destructive";
  return "text-foreground";
}

function formatReturn(value: string | null): string {
  if (value == null) return "N/A";
  const percent = Number(value) * 100;
  const sign = percent > 0 ? "+" : "";
  return `${sign}${percent.toFixed(2)}%`;
}

// RSI is a factual measurement, not a signal — deliberately no green/red or
// any other value-based styling (no "bullish"/"bearish"/"overbought"/
// "oversold" framing). The only distinction made here is real vs.
// unavailable, exactly like every other feature column.
function rsiClass(value: string | null): string {
  return value == null ? "text-muted-foreground/70" : "text-foreground";
}

// A plain number (e.g. "63.42"), never a percentage — RSI is already a
// 0-100 index, not a fraction to be multiplied.
function formatRsi(value: string | null): string {
  if (value == null) return "N/A";
  return Number(value).toFixed(2);
}
