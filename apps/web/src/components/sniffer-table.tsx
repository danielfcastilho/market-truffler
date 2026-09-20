"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import type { SnifferInstrument } from "@/lib/server-api";
import { FEATURE_COLUMNS, groupColumns, type FeatureKey } from "@/lib/sniffer-feature-columns";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type SortColumn = "symbol" | FeatureKey;
type SortDirection = "asc" | "desc";

const GROUPS = groupColumns(FEATURE_COLUMNS);

/**
 * The Sniffs matrix: a horizontally scrollable analytical table, not a
 * fixed-width grid. As more Sniffs are added (more `FEATURE_COLUMNS`
 * entries), the matrix grows wider and scrolls — it never shrinks column
 * content or collapses to pagination. The Symbol column and the header
 * stay pinned via CSS `position: sticky` inside the matrix's own scroll
 * container, so identity and column labels are never lost while scanning
 * across ~771 rows and a growing number of columns. Every Symbol links to
 * `/sniffer/{symbol}` — that navigation is independent of sorting/
 * filtering, which both still operate on the plain `instrument.symbol`
 * string underneath the link.
 *
 * Purely client-side, viewer-local sorting and filtering. This is not a
 * ranking feature: the default is alphabetical by symbol (neutral, implies
 * nothing about desirability), and toggling any column or typing into the
 * filter only reorders/narrows what's already on the page for the person
 * looking at it — it never calls the backend, which has no ranking/sort
 * concept of its own.
 */
export function SnifferTable({ instruments }: { instruments: SnifferInstrument[] }) {
  const [sortColumn, setSortColumn] = useState<SortColumn>("symbol");
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");
  const [filter, setFilter] = useState("");

  const filtered = useMemo(() => {
    const query = filter.trim().toUpperCase();
    if (!query) return instruments;
    return instruments.filter((instrument) => instrument.symbol.toUpperCase().includes(query));
  }, [instruments, filter]);

  const sorted = useMemo(() => {
    const rows = [...filtered];
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
  }, [filtered, sortColumn, sortDirection]);

  function toggleSort(column: SortColumn) {
    if (sortColumn === column) {
      setSortDirection((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortColumn(column);
      setSortDirection("asc");
    }
  }

  return (
    <div className="space-y-3">
      <Input
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
        placeholder="Filter symbol…"
        aria-label="Filter by symbol"
        className="max-w-48"
      />

      <div className="max-h-[70vh] overflow-auto rounded-md border border-border">
        <table className="border-separate border-spacing-0 font-mono text-sm">
          <thead className="sticky top-0 z-20 bg-background">
            <tr className="text-left text-muted-foreground">
              <th
                rowSpan={2}
                scope="col"
                className="sticky left-0 z-30 border-b border-r border-border bg-background pb-2 pl-3 align-bottom"
              >
                <SortableHeader
                  label="Symbol"
                  active={sortColumn === "symbol"}
                  direction={sortDirection}
                  onClick={() => toggleSort("symbol")}
                />
              </th>
              {GROUPS.map((group) => (
                <th
                  key={group.name}
                  colSpan={group.span}
                  scope="colgroup"
                  className="border-b border-border/40 bg-background pb-1 text-center font-medium"
                >
                  {group.name}
                </th>
              ))}
            </tr>
            <tr className="text-left text-muted-foreground">
              {FEATURE_COLUMNS.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  title={column.key}
                  style={{ minWidth: column.minWidthPx }}
                  className="border-b border-border bg-background pb-2 pr-3 text-right"
                >
                  <SortableHeader
                    label={column.label}
                    active={sortColumn === column.key}
                    direction={sortDirection}
                    onClick={() => toggleSort(column.key)}
                    align="right"
                  />
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border/60">
            {sorted.map((instrument) => (
              <tr key={instrument.instrument_id}>
                <td className="sticky left-0 z-10 min-w-40 whitespace-nowrap border-r border-border bg-background py-2 pl-3 pr-4 font-medium">
                  <Link
                    href={`/sniffer/${encodeURIComponent(instrument.symbol)}`}
                    className="hover:underline"
                  >
                    {instrument.symbol}
                  </Link>
                </td>
                {FEATURE_COLUMNS.map((column) => (
                  <td
                    key={column.key}
                    style={{ minWidth: column.minWidthPx }}
                    className={cn(
                      "whitespace-nowrap py-2 pr-3 text-right",
                      column.valueClassName(instrument[column.key]),
                    )}
                  >
                    {column.format(instrument[column.key])}
                  </td>
                ))}
              </tr>
            ))}
            {sorted.length === 0 && (
              <tr>
                <td
                  colSpan={1 + FEATURE_COLUMNS.length}
                  className="py-6 text-center text-muted-foreground/70"
                >
                  No symbols match &quot;{filter}&quot;
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
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
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1 hover:text-foreground",
        align === "right" && "flex-row-reverse",
        active && "text-foreground",
      )}
    >
      {label}
      {active && <span className="text-xs">{direction === "asc" ? "▲" : "▼"}</span>}
    </button>
  );
}
