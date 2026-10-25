import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { LiveRankings, RankingSnapshotDetail, RankingSnapshotSummary } from "@/lib/rankings";

import { RankingBoard } from "./ranking-board";
import {
  ComparisonSummary,
  HistoricalCoinPanel,
  HistoryEmpty,
  HistoryNavigator,
  RankingModeSwitch,
} from "./ranking-history";

vi.mock("next/navigation", () => ({ usePathname: () => "/sniffer" }));

const row = (rank: number, symbol: string, qualified: boolean, checks = true) => ({
  rank,
  symbol,
  score: 1 - rank / 100,
  contributions: [{ feature: "return_1h", transform: "cs_percentile" as const, weight: -1, raw: -0.01, transformed: 0.01, contribution: -0.01 }],
  context: { return_1h: -0.01 },
  qualification: {
    qualified,
    failed: qualified ? [] : ["return_tail"],
    missing: [],
    ...(checks ? { checks: [{ id: "return_tail", feature: "return_1h", measure: "percentile" as const, threshold: { at_most: 0.05 }, observed: 0.01, outcome: qualified ? ("passed" as const) : ("failed" as const) }] } : {}),
  },
});

const spec = { qualification_id: "long-q-v1", name: "q", spec_hash: "q".repeat(64), ranking_candidate: "long-m-v1", direction: "long" as const, policy: "relative", description: "d", min_frame_completeness: 0.95, conditions: [{ id: "return_tail", feature: "return_1h", measure: "percentile" as const, at_most: 0.05, at_least: null, rationale: "r" }], status: "experimental" };

const RANKING: LiveRankings = {
  available: true,
  reason: null,
  frame: { frame_time: "2026-10-25T03:00:00+00:00", status: "complete", completeness: 1, expected_instruments: 3, available_instruments: 3, analyzed_instruments: 3, analyzed_at: "2026-10-25T03:01:10+00:00", age_seconds: 120, stale: false, stale_reason: null },
  long: {
    direction: "long",
    available: true,
    reason: null,
    model: { candidate_id: "long-m-v1", name: "Long model", spec_hash: "m".repeat(64), model_family: "linear_weighted_v1", direction: "long", objective: "o", terms: [], missing_policy: "exclude", validation_status: "experimental" },
    eligible: 3,
    excluded: [],
    degenerate_features: [],
    rows: [row(1, "AAAUSDT", false), row(2, "BBBUSDT", true), row(3, "CCCUSDT", false, false)],
    qualification: { status: "evaluated", reason: null, spec, evaluated: 3, qualified: 1, truffles: [{ rank: 2, symbol: "BBBUSDT", score: 0.98 }] },
  },
  short: { direction: "short", available: false, reason: "candidate x not found", model: null, eligible: null, excluded: [], degenerate_features: [], rows: [], qualification: { status: "unavailable", reason: "x", spec: null, truffles: [] } },
};

const SUMMARY: RankingSnapshotSummary = {
  id: 7,
  frame_time: "2026-10-25T03:00:00+00:00",
  analyzed_at: "2026-10-25T03:01:10+00:00",
  analysis: "live",
  published_at: "2026-10-25T03:01:52+00:00",
  temporal_contract_version: 2,
  long_candidate: "long-m-v1",
  long_candidate_hash: "m".repeat(64),
  short_candidate: "short-m-v1",
  short_candidate_hash: null,
  long_qualification: "long-q-v1",
  long_qualification_hash: "q".repeat(64),
  short_qualification: null,
  short_qualification_hash: null,
  long_qualification_status: "evaluated",
  short_qualification_status: "unavailable",
  long_truffles: 1,
  short_truffles: null,
  schema_version: 1,
  payload_sha256: "p".repeat(64),
};

const DETAIL: RankingSnapshotDetail = {
  snapshot: SUMMARY,
  ranking: RANKING,
  previous: { ...SUMMARY, id: 6, frame_time: "2026-10-25T02:40:00+00:00" },
  next: null,
  comparison: {
    available: true,
    previous_id: 6,
    previous_frame_time: "2026-10-25T02:40:00+00:00",
    frame_gap_seconds: 1200,
    consecutive: false,
    same_frame: false,
    long: {
      available: true,
      k: 10,
      model_changed: false,
      policy_changed: true,
      entered_top_k: ["CCCUSDT"],
      left_top_k: ["ZZZUSDT"],
      movements: {
        AAAUSDT: { rank: 1, previous_rank: 4, change: 3 },
        BBBUSDT: { rank: 2, previous_rank: 1, change: -1 },
        CCCUSDT: { rank: 3, previous_rank: null, change: null },
      },
      truffles: { comparable: false, count: 1, previous_count: 2, count_change: -1, gained: ["BBBUSDT"], lost: ["XXXUSDT", "YYYUSDT"] },
    },
    short: { available: false, reason: "a ranking in one of the snapshots was unavailable" },
  },
};

describe("Ranking history", () => {
  it("always says which mode is active", () => {
    render(<RankingModeSwitch mode="history" />);
    expect(screen.getByRole("link", { name: "history" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "live" })).toHaveAttribute("href", "/sniffer");
  });

  it("shows frame and publication times, neighbours and the way back to Live", () => {
    render(<HistoryNavigator detail={DETAIL} page={{ items: [SUMMARY, DETAIL.previous!], next_before: null }} />);
    const section = screen.getByRole("region", { name: "Snapshot" });
    expect(section).toHaveTextContent("published 2026-10-25 03:01:52 UTC");
    expect(section).toHaveTextContent("not the live one");
    expect(within(section).getByRole("link", { name: /Previous/ })).toHaveAttribute("href", "/sniffer?view=history&snapshot=6");
    expect(section).toHaveTextContent("Newest snapshot");
    expect(within(section).getByRole("link", { name: "Return to Live" })).toHaveAttribute("href", "/sniffer");
  });

  it("reports gaps, spec changes and unavailable comparisons honestly", () => {
    render(<ComparisonSummary comparison={DETAIL.comparison} />);
    const section = screen.getByRole("region", { name: "Changes since the previous snapshot" });
    expect(section).toHaveTextContent("20 min earlier — not a consecutive 5-minute comparison");
    const long = section.querySelector('[data-comparison="long"]') as HTMLElement;
    expect(long).toHaveTextContent("policy changed — not directly comparable");
    expect(long).toHaveTextContent("entered CCCUSDT · left ZZZUSDT");
    expect(long).toHaveTextContent("Truffles 2 → 1 · newly qualified BBBUSDT · no longer qualified XXXUSDT, YYYUSDT");
    expect(section).toHaveTextContent("Short: comparison unavailable");
  });

  it("renders the stored board with movements and links that stay in the snapshot", () => {
    render(
      <RankingBoard
        result={{ status: "ok", data: RANKING }}
        history={{ snapshotId: 7, publishedAt: SUMMARY.published_at, comparison: DETAIL.comparison }}
      />,
    );
    expect(screen.getByText(/Published snapshot/)).toBeInTheDocument();
    expect(screen.queryByText("Current")).toBeNull();
    expect(document.querySelector('[data-movement="AAAUSDT"]')).toHaveTextContent("↑ 3");
    expect(document.querySelector('[data-movement="BBBUSDT"]')).toHaveTextContent("↓ 1");
    expect(document.querySelector('[data-movement="CCCUSDT"]')).toHaveTextContent("new to top 10");
    expect(screen.getByRole("link", { name: "BBBUSDT" })).toHaveAttribute(
      "href",
      "/sniffer?view=history&snapshot=7&symbol=BBBUSDT&direction=long",
    );
    const bbb = screen.getAllByRole("row").find((r) => r.getAttribute("data-symbol") === "BBBUSDT")!;
    expect(within(bbb).getByText("🟢 Truffle")).toBeInTheDocument();
  });

  it("inspects a coin as published, never with live values", () => {
    render(<HistoricalCoinPanel detail={DETAIL} symbol="BBBUSDT" />);
    const long = document.querySelector('[data-direction="long"]') as HTMLElement;
    expect(long).toHaveTextContent("Rank #2 / 3");
    expect(long).toHaveTextContent("Truffle");
    expect(screen.getByRole("region", { name: "BBBUSDT in this snapshot" })).toHaveTextContent("as published 2026-10-25 03:01:52 UTC");
    expect(document.querySelector('[data-direction="short"]')).toHaveTextContent("Ranking unavailable in this snapshot.");
  });

  it("says when an explanation was not captured", () => {
    render(<HistoricalCoinPanel detail={DETAIL} symbol="CCCUSDT" />);
    expect(screen.getByText("Qualification explanation not captured.")).toBeInTheDocument();
  });

  it("explains an empty history without fabricating snapshots", () => {
    render(<HistoryEmpty />);
    expect(screen.getByText(/Earlier rankings were never stored and are not reconstructed/)).toBeInTheDocument();
  });
});
