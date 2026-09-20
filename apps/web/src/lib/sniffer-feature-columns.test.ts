import { describe, expect, it } from "vitest";
import { FEATURE_COLUMNS, groupColumns, groupColumnsByFamily } from "./sniffer-feature-columns";

describe("groupColumns", () => {
  it("collapses consecutive same-group columns into one spanning run", () => {
    const runs = groupColumns([
      { group: "A" },
      { group: "A" },
      { group: "B" },
      { group: "B" },
      { group: "B" },
      { group: "C" },
    ]);
    expect(runs).toEqual([
      { name: "A", span: 2 },
      { name: "B", span: 3 },
      { name: "C", span: 1 },
    ]);
  });

  it("keeps non-consecutive occurrences of the same name as separate runs", () => {
    const runs = groupColumns([{ group: "A" }, { group: "B" }, { group: "A" }]);
    expect(runs).toEqual([
      { name: "A", span: 1 },
      { name: "B", span: 1 },
      { name: "A", span: 1 },
    ]);
  });

  it("handles an empty column list", () => {
    expect(groupColumns([])).toEqual([]);
  });

  it("is generic: a future family added to an arbitrary column list needs no other change", () => {
    const columns = [
      { group: "Returns" },
      { group: "Returns" },
      { group: "RSI" },
      { group: "RSI" },
      { group: "RSI" },
      { group: "RSI" },
      { group: "Volatility" },
      { group: "Volatility" },
      { group: "Volatility" },
    ];
    const runs = groupColumns(columns);
    expect(runs).toEqual([
      { name: "Returns", span: 2 },
      { name: "RSI", span: 4 },
      { name: "Volatility", span: 3 },
    ]);
    expect(runs.reduce((sum, r) => sum + r.span, 0)).toBe(columns.length);
  });
});

describe("groupColumnsByFamily", () => {
  it("groups consecutive same-group columns while keeping the full column objects", () => {
    const columns = [
      { group: "A", label: "a1" },
      { group: "A", label: "a2" },
      { group: "B", label: "b1" },
    ];
    expect(groupColumnsByFamily(columns)).toEqual([
      { name: "A", columns: [columns[0], columns[1]] },
      { name: "B", columns: [columns[2]] },
    ]);
  });

  it("is what groupColumns' span counts are derived from", () => {
    const byFamily = groupColumnsByFamily(FEATURE_COLUMNS);
    const spans = groupColumns(FEATURE_COLUMNS);
    expect(byFamily.map((g) => ({ name: g.name, span: g.columns.length }))).toEqual(spans);
  });
});

describe("FEATURE_COLUMNS", () => {
  it("keeps today's six canonical Feature keys, grouped as Returns/RSI", () => {
    expect(FEATURE_COLUMNS.map((c) => c.key)).toEqual([
      "return_5m",
      "return_1h",
      "rsi_14_5m",
      "rsi_14_15m",
      "rsi_14_1h",
      "rsi_14_4h",
    ]);
    expect(groupColumns(FEATURE_COLUMNS)).toEqual([
      { name: "Returns", span: 2 },
      { name: "RSI", span: 4 },
    ]);
  });

  it("gives every column a positive minimum width so labels/values can never collide", () => {
    for (const column of FEATURE_COLUMNS) {
      expect(column.minWidthPx).toBeGreaterThan(0);
    }
  });
});
