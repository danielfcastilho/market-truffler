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
      { name: "shard_s15m_20261012", kind: "daily_shard", manifest: "present", data_file_present: true, temporal_contract_version: null, manifest_problems: [], verification: "never_checked" },
      { name: "shard_s15m_20261013", kind: "daily_shard", manifest: "missing", data_file_present: true, verification: "never_checked" },
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
    expect(shard).toHaveTextContent("contract unrecorded · never checked");
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

  it("shows verification, recent continuity and a registration's actionable alerts", () => {
    const health: DataHealth = {
      ...HEALTH,
      verification: {
        available: true,
        interval_hours: 24,
        last_checked_at: "2026-10-25T03:00:00+00:00",
        last_pass_at: "2026-10-25T03:00:00+00:00",
        last_pass_checked: 2,
        counts: { verified: 2, restricted: 1, failed: 1 },
        scopes: {},
      },
      recent_continuity: {
        available: true,
        windows: {
          "24h": { from: "2026-10-24T03:00:00+00:00", to: "2026-10-25T03:00:00+00:00", window_shortened: false, expected: 288, finalized: 280, missing: 8, never_finalized: 0, coverage: 280 / 288, analysis: { finalized_with_members: 280, analyzed: 279, pending: 1, completion: 279 / 280 } },
          "7d": { from: "2026-10-23T20:00:00+00:00", to: "2026-10-25T03:00:00+00:00", window_shortened: true, expected: 400, finalized: 300, missing: 100, never_finalized: 0, coverage: 0.75, analysis: { finalized_with_members: 300, analyzed: 300, pending: 0, completion: 1 } },
        },
      },
      alerts: [
        { id: "truffle-x-v1/validation/shard_unrecoverable", registration_id: "truffle-x-v1", role: "validation", condition: "shard_unrecoverable", severity: "warning", explanation: "2 completed day(s) of this partition have no shard", days: ["2026-12-28"], interval: ["2026-12-28", "2026-12-29"], first_observed: null, last_checked: null, state: "active" },
        { id: "oi-price-1h-prospective-v1/validation/awaiting_data", registration_id: "oi-price-1h-prospective-v1", role: "validation", condition: "awaiting_data", severity: "info", explanation: "awaiting window", days: [], interval: null, first_observed: null, last_checked: null, state: "active" },
      ],
    };
    render(<DataHealthView health={health} />);
    expect(screen.getByText(/2 verified · 1 restricted \(hash only\) · 1 failed/)).toBeInTheDocument();
    expect(screen.getByText(/Integrity only/)).toBeInTheDocument();
    const day = document.querySelector('[data-window="24h"]') as HTMLElement;
    expect(day).toHaveTextContent("97.2%");
    expect(day).toHaveTextContent("1 still within their analysis window");
    expect(document.querySelector('[data-window="7d"]')).toHaveTextContent("Starts at the first recorded frame");
    const truffle = document.querySelector('[data-registration="truffle-x-v1"]') as HTMLElement;
    expect(within(truffle).getByLabelText("Continuity alerts")).toHaveTextContent("have no shard");
    const existing = document.querySelector('[data-registration="oi-price-1h-prospective-v1"]') as HTMLElement;
    expect(within(existing).queryByLabelText("Continuity alerts")).toBeNull(); // info is not actionable
  });

  it("says when verification has never been reported", () => {
    render(<DataHealthView health={{ ...HEALTH, verification: { available: false, reason: "the research worker has not reported verification yet" } }} />);
    expect(screen.getByText(/Verification unavailable: the research worker/)).toBeInTheDocument();
  });

  it("reports backup protection as not configured instead of protected", () => {
    render(<DataHealthView health={{ ...HEALTH, backups: { available: true, configured: false, reason: "no independent backup destination is configured (RESEARCH_BACKUP_DIR)" } }} />);
    const section = screen.getByRole("region", { name: "Research backups" });
    expect(within(section).getByRole("alert")).toHaveTextContent("Not protected");
  });

  it("shows verified backup and recovery-test states separately", () => {
    render(
      <DataHealthView
        health={{
          ...HEALTH,
          backups: {
            available: true,
            configured: true,
            destination_kind: "operator-initialized mount",
            last_attempt_at: "2026-10-25T02:00:00+00:00",
            last_success_at: "2026-10-25T02:00:00+00:00",
            destination_error: null,
            items_in_scope: 9,
            protected: 8,
            unprotected: 1,
            bytes: 22_000_000,
            failed_items: 1,
            integrity_failures: 0,
            recovery_test: { status: "passed", tested_at: "2026-10-25T02:01:00+00:00", scope: "full", items_tested: 8, restricted_items: 0, failures: 0, error: null },
          },
        }}
      />,
    );
    const section = screen.getByRole("region", { name: "Research backups" });
    expect(section).toHaveTextContent("8/9 items verified");
    expect(section).toHaveTextContent("passed");
    expect(section).toHaveTextContent("0 integrity · 1 other");
    expect(within(section).queryByRole("button")).toBeNull(); // no restore action
  });
});

describe("DataHealthView layout", () => {
  const verification = (failed: number): DataHealth["verification"] => ({
    available: true,
    interval_hours: 24,
    last_checked_at: "2026-10-25T03:00:00+00:00",
    last_pass_at: "2026-10-25T03:00:00+00:00",
    last_pass_checked: 2,
    counts: failed ? { verified: 1, failed } : { verified: 2 },
    scopes: {},
  });

  it("puts readiness first and storage internals in a collapsed section", () => {
    render(<DataHealthView health={{ ...HEALTH, verification: verification(0) }} />);
    const details = screen.getByText("Storage and integrity details").closest("details")!;
    expect(details).not.toHaveAttribute("open");
    expect(within(details).getByRole("heading", { name: "Shard verification" })).toBeInTheDocument();
    expect(within(details).getByRole("heading", { name: "Temporal contract" })).toBeInTheDocument();
    const sections = [...document.querySelectorAll("section[aria-label]")].map((s) => s.getAttribute("aria-label"));
    expect(sections.indexOf("Prospective readiness")).toBeLessThan(sections.indexOf("Verification"));
  });

  it("never tucks a failed integrity check away", () => {
    render(<DataHealthView health={{ ...HEALTH, verification: verification(1) }} />);
    const details = screen.getByText("Storage and integrity details").closest("details")!;
    const heading = screen.getByRole("heading", { name: "Shard verification" });
    expect(details.contains(heading)).toBe(false);
  });
});
