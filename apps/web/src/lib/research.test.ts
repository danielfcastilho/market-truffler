import { describe, expect, it } from "vitest";

import { COMPARISON_RUN, INSUFFICIENT_RUN } from "@/components/research/test-fixtures";
import {
  describeCondition,
  describeFeature,
  describeLabel,
  evidenceFigures,
  formatReturn,
  formatSpread,
  latestBySegment,
  technicalCondition,
  verdictOf,
} from "@/lib/research";

describe("definitions in words", () => {
  it("translates percentile conditions without changing their meaning", () => {
    const bottom = { feature: "return_1h", transform: "cs_percentile", op: "<=", value: 0.2 } as const;
    expect(describeCondition(bottom)).toBe(
      "1-hour return is in the bottom 20% of instruments in the same frame",
    );
    expect(technicalCondition(bottom)).toBe("cs_percentile(return_1h) <= 0.2");
    expect(describeCondition({ ...bottom, op: ">=", value: 0.8 })).toBe(
      "1-hour return is in the top 20% of instruments in the same frame",
    );
    expect(describeCondition({ ...bottom, op: "<", value: 0.1 })).toContain("ranks below the 10% mark");
  });

  it("translates raw conditions, naming sign thresholds", () => {
    const oi = { feature: "oi_change_1h", transform: "raw", op: "<", value: 0 } as const;
    expect(describeCondition(oi)).toBe("1-hour Open Interest change is negative (below 0)");
    expect(describeCondition({ ...oi, op: ">" })).toBe("1-hour Open Interest change is positive (above 0)");
    expect(describeCondition({ ...oi, feature: "rsi_14_4h", op: ">=", value: 70 })).toBe(
      "4-hour RSI(14) is at least 70",
    );
  });

  it("leaves unknown features and labels untouched rather than guessing", () => {
    expect(describeFeature("mystery_metric")).toBe("mystery_metric");
    expect(describeLabel("something_else")).toBe("something_else");
    expect(describeLabel("forward_return_4h")).toBe("Next 4-hour return");
    expect(describeLabel("forward_relative_return_1h")).toContain("relative to the market");
  });
});

describe("numbers", () => {
  it("formats returns and spreads with explicit signs and units", () => {
    expect(formatReturn(-0.00159)).toBe("−0.159%");
    expect(formatReturn(null)).toBe("—");
    expect(formatSpread(0.0001)).toBe("+0.010 pp");
    expect(formatSpread(0)).toBe("0.000 pp");
  });
});

describe("runs", () => {
  it("reads verdicts only from completed runs with a known verdict", () => {
    expect(verdictOf(INSUFFICIENT_RUN)).toBe("insufficient_data");
    expect(verdictOf({ ...INSUFFICIENT_RUN, status: "failed" })).toBeNull();
    expect(
      verdictOf({ ...INSUFFICIENT_RUN, report: { verdict: "promising" as never } }),
    ).toBeNull();
  });

  it("keeps the newest run per segment, never merging them", () => {
    const older = { ...INSUFFICIENT_RUN, run_id: "older" };
    const validation = { ...INSUFFICIENT_RUN, run_id: "val", segment: "validation" };
    const latest = latestBySegment([INSUFFICIENT_RUN, older, validation]);
    expect(latest.development.run_id).toBe(INSUFFICIENT_RUN.run_id);
    expect(latest.validation.run_id).toBe("val");
  });

  it("names experiment and comparison figures for what they are", () => {
    const experiment = evidenceFigures(INSUFFICIENT_RUN);
    expect(experiment).toMatchObject({ kind: "experiment", days: 1, selectedRows: 3703 });
    expect(experiment.baselineLabel).toBe("Eligible universe mean");
    const comparison = evidenceFigures(COMPARISON_RUN);
    expect(comparison).toMatchObject({ kind: "comparison", selectedRows: 2138, spread: 0.0001 });
    expect(comparison.baselineLabel).toBe("Reference group mean");
  });
});
