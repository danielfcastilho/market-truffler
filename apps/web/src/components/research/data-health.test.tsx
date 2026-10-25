import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DailyCoverage, DataHealth } from "@/lib/research";

import { DataHealthView } from "./data-health";

function day(d: string, extra: Partial<DailyCoverage> = {}): DailyCoverage {
  return {
    day: d,
    partial_day: null,
    expected: 288,
    finalized: 288,
    complete: 288,
    partial: 0,
    building: 0,
    missing: 0,
    analyzed: 288,
    unanalyzed: 0,
    analysis_unknown: 0,
    coverage: 1,
    largest_gap_minutes: 0,
    analyzed_by_contract: { v1: 0, v2: 288 },
    ...extra,
  };
}

const HEALTH: DataHealth = {
  computed_at: "2026-10-25T02:30:00+00:00",
  cache_seconds: 60,
  frames: {
    available: true,
    observed_from: "2026-10-23T20:00:00+00:00",
    observed_to: "2026-10-25T02:30:00+00:00",
    observed_note: "from the first on-grid frame",
    expected: 378,
    finalized: 300,
    missing: 78,
    building: 0,
    partial: 1,
    complete: 299,
    analyzed: 298,
    unanalyzed: 2,
    analysis_unknown: 0,
    coverage: 300 / 378,
    latest_frame: "2026-10-25T02:25:00+00:00",
    off_grid_frames: 762,
    duplicate_rows: 0,
    results_retained_from: "2026-09-19T19:29:00+00:00",
    covered_intervals: { count: 2, truncated: false, items: [] },
    missing_intervals: { count: 1, truncated: false, items: [["2026-10-24T03:00:00+00:00", "2026-10-24T09:30:00+00:00", 78]] },
    analyzed_by_contract: { v1: { frames: 0, first: null, last: null }, v2: { frames: 298, first: null, last: null } },
    daily: [
      day("2026-10-23", { partial_day: "first", expected: 48, finalized: 48 }),
      day("2026-10-24", { finalized: 210, missing: 78, coverage: 210 / 288, largest_gap_minutes: 390 }),
      day("2026-10-25", { partial_day: "last", expected: 30, finalized: 30 }),
    ],
  },
  shards: {
    datasets: [
      { name: "shard_s15m_20261012", kind: "daily_shard", manifest: "present", data_file_present: true, temporal_contract_version: null, manifest_problems: [], verification: "not_checked" },
      { name: "shard_s15m_20261013", kind: "daily_shard", manifest: "missing", data_file_present: true, verification: "not_checked" },
    ],
    overlaps: [],
  },
  shard_continuity: { available: true, reported_at: null, shards_start: "2026-10-12T15:00:00+00:00", days: 3, by_status: { preserved: { count: 2, days: [] }, collection_gap: { count: 1, days: [] } }, status_meanings: {} },
  temporal_contract: { current_version: 2, frames: null, datasets: { unrecorded: { count: 1, names: ["shard_s15m_20261012"] } }, meaning: {} },
  readiness: {
    available: true,
    reported_at: "2026-10-25T02:00:00+00:00",
    registrations: [
      {
        registration_id: "oi-price-1h-prospective-v1",
        kind: "campaign_claims",
        registration_hash: "a".repeat(64),
        verification: "verified",
        problems: [],
        partitions: [
          { role: "validation", start: "2026-10-26", end: "2026-11-25", state: "awaiting_data", phase: "awaiting_window", eligible_from: "2026-11-27T00:00:00+00:00", days: 30, days_with_shard: 0, days_elapsed: 0, missing_past_days: [], blocking: ["not eligible until 2026-11-27T00:00:00+00:00"], eligible_now: false },
        ],
      },
      {
        registration_id: "truffle-x-v1",
        kind: "truffle_qualification",
        registration_hash: "b".repeat(64),
        verification: "invalid",
        problems: ["registration_hash does not match the registration: it was modified"],
        partitions: [
          { role: "validation", start: "2026-12-27", end: "2027-01-26", state: "awaiting_data", phase: "accumulating", eligible_from: "2027-01-28T00:00:00+00:00", days: 30, days_with_shard: 3, days_elapsed: 5, missing_past_days: ["2026-12-28", "2026-12-29"], blocking: ["the registration does not verify", "not eligible until 2027-01-28T00:00:00+00:00"], eligible_now: false },
        ],
      },
    ],
  },
  warnings: ["78 of 378 grid frames are missing since 2026-10-23 (collection was not running)."],
  limitations: ["Data completeness describes collection, not scientific validity."],
};

describe("DataHealthView", () => {
  it("shows coverage metrics, issues and the not-validity disclaimer", () => {
    render(<DataHealthView health={HEALTH} />);
    expect(screen.getByText("79.4%")).toBeInTheDocument();
    expect(screen.getByText(/300 of 378 grid frames/)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("78 of 378 grid frames are missing");
    expect(screen.getByText(/not evidence of anything/)).toBeInTheDocument();
  });

  it("marks partial boundary days apart from genuine gaps", () => {
    render(<DataHealthView health={HEALTH} />);
    const first = document.querySelector('[data-day="2026-10-23"]') as HTMLElement;
    expect(first).toHaveTextContent("partial day (first)");
    expect(first).toHaveTextContent("48/48");
    const gap = document.querySelector('[data-day="2026-10-24"]') as HTMLElement;
    expect(gap).not.toHaveTextContent("partial day");
    expect(gap.className).toContain("bg-destructive");
    expect(gap).toHaveTextContent("390 min");
    expect(screen.getByText(/762 frames off the 5-minute grid/)).toBeInTheDocument();
  });

  it("shows readiness phases, missing shards and verification problems without results", () => {
    render(<DataHealthView health={HEALTH} />);
    const existing = document.querySelector('[data-registration="oi-price-1h-prospective-v1"]') as HTMLElement;
    expect(within(existing).getByText("Awaiting its window")).toBeInTheDocument();
    const truffle = document.querySelector('[data-registration="truffle-x-v1"]') as HTMLElement;
    expect(within(truffle).getByText("Accumulating data")).toBeInTheDocument();
    expect(within(truffle).getByRole("alert")).toHaveTextContent("it was modified");
    expect(truffle).toHaveTextContent("2 elapsed day(s) missing");
    expect(truffle).toHaveTextContent("the registration does not verify");
    expect(screen.queryByText(/consistent_with_improvement|positive_evidence/)).toBeNull();
  });

  it("never presents unchecked or missing metadata as verified or zero", () => {
    render(<DataHealthView health={HEALTH} />);
    const shard = document.querySelector('[data-dataset="shard_s15m_20261012"]') as HTMLElement;
    expect(shard).toHaveTextContent("contract unrecorded · not verified this pass");
    expect(document.querySelector('[data-dataset="shard_s15m_20261013"]')).toHaveTextContent("manifest missing");
  });

  it("states unavailable sources instead of showing zeros", () => {
    const empty: DataHealth = {
      ...HEALTH,
      frames: { available: false, reason: "no completed frame on the 5-minute grid yet", off_grid_frames: 0 },
      shards: { datasets: [], overlaps: [] },
      shard_continuity: { available: false, reason: "the research worker has not reported yet" },
      readiness: { available: false, reason: "the research worker has not reported registration readiness yet", registrations: [] },
      warnings: [],
    };
    render(<DataHealthView health={empty} />);
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
    expect(screen.getByText("No research dataset exists.")).toBeInTheDocument();
    expect(screen.getByText(/Readiness unavailable: the research worker/)).toBeInTheDocument();
    expect(screen.getByText(/Shard continuity unavailable/)).toBeInTheDocument();
  });
});
