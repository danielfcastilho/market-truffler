import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { describeTerm, type RankingCandidate, type RankingResearch as Research, type RankingRun } from "@/lib/research";

import { RankingResearch } from "./ranking-research";
import { ResearchNav } from "./research-nav";

const pathname = vi.hoisted(() => ({ value: "/oink-corp/ranking" }));
vi.mock("next/navigation", () => ({ usePathname: () => pathname.value }));

const PRICE = { feature: "return_1h", transform: "cs_percentile", weight: -1 } as const;
const OI = { feature: "oi_change_1h", transform: "cs_percentile", weight: -0.5 } as const;

function model(id: string, name: string, direction: "long" | "short", terms: RankingCandidate["terms"]): RankingCandidate {
  return { candidate_id: id, name, model_family: "linear_weighted_v1", direction, objective: "o", terms, missing_policy: "exclude" };
}

const SUMMARY = (q: number) => ({
  mean_top_k_excess_utility: q,
  timestamps: 24,
  distinct_top_symbols: 182,
  top_k_missing_outcome: 15,
  diagnostics: { mean_top_k_utility: -0.0022, mean_top_bottom_separation: -0.0038, mean_spearman: -0.1074, daily_mean_excess_utility: {} },
});

const RUN: RankingRun = {
  run_id: "20261024T203608443251Z-fc261a",
  experiment_id: "long-oi-vs-price-v1",
  executed_at: "2026-10-24T20:36:08+00:00",
  status: "completed",
  spec_hash: "a".repeat(64),
  repeat_of: [],
  ranking_evaluator_version: 1,
  candidate: { candidate_id: "long-oi-v1", spec_hash: "b".repeat(64) },
  reference: { candidate_id: "long-price-v1", spec_hash: "c".repeat(64) },
  datasets: [{ name: "shard_s15m_20261012", data_sha256: "d".repeat(64) }],
  report: {
    segment: "development",
    primary_metric: "top_k_excess_utility",
    top_k: 20,
    horizon_minutes: 60,
    days: ["2026-10-12"],
    coverage: { timestamps: 24, timestamps_universe_too_small: 0, mean_universe: 795, distinct_days: 1 },
    candidate: SUMMARY(-0.00206),
    reference: SUMMARY(-0.00112),
    primary: {
      estimand: "paired",
      mean: -0.00094,
      matched_timestamps: 24,
      uncertainty: { suppressed: true, reason: "fewer than 10 qualifying days" },
      verdict: "insufficient_data",
      verdict_reasons: ["1 qualifying days in segment (< 10)"],
    },
    verdict: "insufficient_data",
    verdict_reasons: ["1 qualifying days in segment (< 10)"],
    disclosures: ["Weights are manually specified research parameters; nothing was optimized."],
  },
};

const RESEARCH: Research = {
  candidates: [],
  studies: [
    {
      study: {
        study_id: "long-oi-vs-price-v1",
        name: "Long: does Open Interest improve a price-reversal ranking?",
        question: "Do the top 20 …",
        candidate: "long-oi-v1",
        reference: "long-price-v1",
        datasets: [],
        label: "forward_relative_return_1h",
        top_k: 20,
      },
      candidate: model("long-oi-v1", "Long — price reversal + falling Open Interest", "long", [PRICE, OI]),
      reference: model("long-price-v1", "Long — price reversal (price only)", "long", [PRICE]),
      runs: [RUN],
    },
    {
      study: {
        study_id: "short-oi-vs-price-v1",
        name: "Short: does Open Interest improve a price-reversal ranking?",
        question: "q",
        candidate: "short-oi-v1",
        reference: null,
        datasets: [],
        label: "forward_relative_return_1h",
        top_k: 20,
      },
      candidate: model("short-oi-v1", "Short — price reversal", "short", [{ ...PRICE, weight: 1 }]),
      reference: null,
      runs: [],
    },
  ],
  issues: [],
};

describe("RankingResearch", () => {
  it("states plainly that this is unoptimized, unvalidated development research", () => {
    render(<RankingResearch research={RESEARCH} />);
    expect(screen.getByRole("note")).toHaveTextContent(
      /weights are chosen by hand; nothing has been optimized.*no candidate is validated, no Champion model exists.*none of this is a live ranking or a trading signal/i,
    );
  });

  it("shows each model's features, transforms and weights, Long and Short apart", () => {
    render(<RankingResearch research={RESEARCH} />);
    const long = within(document.getElementById("long-oi-vs-price-v1")!);
    expect(long.getByText("Long ↗")).toBeInTheDocument();
    expect(long.getByText("Long — price reversal (price only)")).toBeInTheDocument();
    expect(long.getByText("−0.5 × percentile rank within the frame of 1-hour open interest change")).toBeInTheDocument();
    expect(long.getByText("-0.5 × cs_percentile(oi_change_1h)")).toBeInTheDocument();
    const short = within(document.getElementById("short-oi-vs-price-v1")!);
    expect(short.getByText("Short ↘")).toBeInTheDocument();
    expect(short.getByText("Registered but never run — no evidence exists.")).toBeInTheDocument();
  });

  it("presents insufficient data honestly, with paired results as observations only", () => {
    render(<RankingResearch research={RESEARCH} />);
    const long = within(document.getElementById("long-oi-vs-price-v1")!);
    // In the latest-evidence panel and in the (collapsed) run history.
    expect(long.getAllByText("Insufficient data")).toHaveLength(2);
    expect(long.getByText("Descriptive observations — not evidence")).toBeInTheDocument();
    expect(long.getByText("Reference: top-K excess utility").nextSibling).toHaveTextContent("−0.112 pp");
    expect(long.getByText("Candidate: top-K excess utility").nextSibling).toHaveTextContent("−0.206 pp");
    expect(long.getByText("Paired difference (candidate − reference)").nextSibling).toHaveTextContent("−0.094 pp");
    expect(long.getByText(/No uncertainty interval: fewer than 10 qualifying days/)).toBeInTheDocument();
    expect(long.getByText(/Exploratory diagnostics \(never decide a result\)/)).toBeInTheDocument();
  });

  it("shows a recorded interval for an evidence verdict, and marks repeats", () => {
    const positive: RankingRun = {
      ...RUN,
      repeat_of: ["long-oi-vs-price-v1/earlier"],
      report: {
        ...RUN.report!,
        primary: {
          ...RUN.report!.primary,
          verdict: "positive_evidence",
          verdict_reasons: [],
          uncertainty: { suppressed: false, confidence_level: 0.9, interval: [0.0004, 0.0021], method: "day-block bootstrap" },
        },
        verdict: "positive_evidence",
      },
    };
    render(<RankingResearch research={{ ...RESEARCH, studies: [{ ...RESEARCH.studies[0], runs: [positive] }] }} />);
    expect(screen.getByText(/90% interval: \[\+0.040 pp, \+0.210 pp\]/)).toBeInTheDocument();
    expect(screen.queryByText("Descriptive observations — not evidence")).not.toBeInTheDocument();
    expect(screen.getByText(/repeat of 1 earlier run\(s\) — not new evidence/)).toBeInTheDocument();
  });

  it("has empty and failed states", () => {
    const { unmount } = render(<RankingResearch research={{ candidates: [], studies: [], issues: [] }} />);
    expect(screen.getByText("No ranking study has been registered yet.")).toBeInTheDocument();
    unmount();
    const failed = { ...RUN, status: "failed", error: "ExperimentSpecError: reserved for the holdout", report: undefined };
    render(<RankingResearch research={{ ...RESEARCH, studies: [{ ...RESEARCH.studies[0], runs: [failed] }] }} />);
    expect(screen.getByText(/No evaluation was produced: ExperimentSpecError: reserved for the holdout/)).toBeInTheDocument();
  });
});

describe("ResearchNav", () => {
  it("separates feature research, ranking research and history", () => {
    render(<ResearchNav />);
    expect(screen.getByRole("link", { name: "Ranking research" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Feature research" })).not.toHaveAttribute("aria-current");
  });

  it("treats campaign and experiment pages as feature research", () => {
    pathname.value = "/oink-corp/campaigns/x";
    render(<ResearchNav />);
    expect(screen.getByRole("link", { name: "Feature research" })).toHaveAttribute("aria-current", "page");
  });
});

describe("describeTerm", () => {
  it("states sign, weight, transform and feature", () => {
    expect(describeTerm({ feature: "return_1h", transform: "cs_robust_z", weight: 2 })).toBe(
      "+2 × robust z-score within the frame of 1-hour return",
    );
  });
});
