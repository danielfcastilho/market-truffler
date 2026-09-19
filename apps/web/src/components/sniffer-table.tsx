"use client";

import { useMemo, useState } from "react";
import type { SnifferInstrument } from "@/lib/server-api";
import { cn } from "@/lib/utils";

type SortColumn = "symbol" | "return_5m";
type SortDirection = "asc" | "desc";

/**
 * Purely client-side, viewer-local sorting. This is not a ranking feature:
 * the default is alphabetical by symbol (neutral, implies nothing about
 * desirability), and toggling the return_5m column only reorders what's
 * already on the page for the person looking at it — it never calls the
 * backend, which has no ranking/sort concept of its own.
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
        const aValue = a.return_5m != null ? Number(a.return_5m) : null;
        const bValue = b.return_5m != null ? Number(b.return_5m) : null;
        if (aValue == null && bValue == null) comparison = 0;
        else if (aValue == null) comparison = 1; // unavailable sorts last, either direction
        else if (bValue == null) comparison = -1;
        else comparison = aValue - bValue;
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
          <SortableHeader
            label="return_5m"
            active={sortColumn === "return_5m"}
            direction={sortDirection}
            onClick={() => toggleSort("return_5m")}
            align="right"
          />
        </tr>
      </thead>
      <tbody className="divide-y divide-border/60">
        {sorted.map((instrument) => (
          <tr key={instrument.instrument_id}>
            <td className="py-2 pr-4 font-medium">{instrument.symbol}</td>
            <td className={cn("py-2 text-right", returnClass(instrument.return_5m))}>
              {formatReturn(instrument.return_5m)}
            </td>
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
