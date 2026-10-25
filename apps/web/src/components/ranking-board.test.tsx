import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { DirectionRanking, LiveRankings, RankedCoin } from "@/lib/rankings";

import { RankingBoard } from "./ranking-board";
import { RankingContext } from "./ranking-context";
import { SnifferNav } from "./sniffer-nav";

const pathname = vi.hoisted(() => ({ value: "/sniffer" }));
vi.mock("next/navigation", () => ({ usePathname: () => pathname.value }));

function coin(rank: number, symbol: string, priceC: number, oiC: number): RankedCoin {
  return {
    rank,
    symbol,
    score: priceC + oiC,
    contributions: [
      { feature: "return_1h", transform: "cs_percentile", weight: -1, raw: -0.0123, transformed: -priceC, contribution: priceC },
      { feature: "oi_change_1h", transform: "cs_percentile", weight: -0.5, raw: -0.02, transformed: -2 * oiC, contribution: oiC },
    ],
    context: { return_1h: -0.0123, oi_change_1h: -0.02, relative_volume_1h: 2.4 },
  };
}

function ranking(direction: "long" | "short", rows: RankedCoin[]): DirectionRanking {
  return {
    direction,
    available: true,
    reason: null,
    model: {
      candidate_id: `${direction}-price-reversal-oi-v1`,
      name: `${direction === "long" ? "Long" : "Short"} — price reversal + falling Open Interest`,
      spec_hash: "a".repeat(64),
      model_family: "linear_weighted_v1",
      direction,
      objective: "o",
      terms: [
        { feature: "return_1h", transform: "cs_percentile", weight: direction === "long" ? -1 : 1 },
        { feature: "oi_change_1h", transform: "cs_percentile", weight: -0.5 },
      ],
      missing_policy: "exclude",
      validation_status: "experimental — manually specified, not optimized, not independently validated, not a Champion",
    },
    eligible: 795,
    excluded: [{ symbol: "NEWUSDT", missing: ["oi_change_1h"] }],
    degenerate_features: [],
    rows,
  };
}

// Deliberately not alphabetical: the board must keep backend order.
const DATA: LiveRankings = {
  available: true,
  reason: null,
  frame: {
    frame_time: "2026-10-24T20:55:00+00:00",
    status: "complete",
    completeness: 1,
    expected_instruments: 808,
    available_instruments: 808,
    analyzed_instruments: 808,
    analyzed_at: "2026-10-24T20:55:30+00:00",
    age_seconds: 60,
    stale: false,
    stale_reason: null,
  },
  long: ranking("long", [coin(1, "ZECUSDT", 0.9, 0.4), coin(2, "ADAUSDT", 0.8, 0.3), coin(3, "BTCUSDT", 0.5, 0.1)]),
  short: ranking("short", [coin(1, "BTCUSDT", 0.7, 0.2), coin(2, "ZECUSDT", 0.1, 0.0)]),
};
const OK = { status: "ok" as const, data: DATA };

function symbols() {
  return screen.getAllByRole("row").slice(1).map((r) => r.getAttribute("data-symbol")).filter(Boolean);
}

describe("RankingBoard", () => {
  it("shows the live frame, one concise disclosure, and the backend order", () => {
    render(<RankingBoard result={OK} />);
    expect(screen.getByText(/Frame 20:55 UTC · 808 instruments/)).toBeInTheDocument();
    expect(screen.getByText("Current")).toBeInTheDocument();
    expect(screen.getByText(/not independently validated — a high rank is not a probability of profit/)).toBeInTheDocument();
    expect(symbols()).toEqual(["ZECUSDT", "ADAUSDT", "BTCUSDT"]);
    expect(screen.getByRole("button", { name: "🟢 LONG" })).toHaveAttribute("aria-pressed", "true");
  });

  it("says when the frame's analysis was recovered after a restart", () => {
    render(<RankingBoard result={{ status: "ok", data: { ...DATA, frame: { ...DATA.frame!, analysis: "recovery" } } }} />);
    expect(screen.getByText(/analysis recovered after a restart/)).toBeInTheDocument();
  });

  it("switches between Long and Short rankings of the same frame", () => {
    render(<RankingBoard result={OK} />);
    fireEvent.click(screen.getByRole("button", { name: "🔴 SHORT" }));
    expect(screen.getByRole("button", { name: "🔴 SHORT" })).toHaveAttribute("aria-pressed", "true");
    expect(symbols()).toEqual(["BTCUSDT", "ZECUSDT"]);
    expect(screen.getByText("Short — price reversal + falling Open Interest")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "BTCUSDT" })).toHaveAttribute("href", "/sniffer/BTCUSDT?direction=short");
  });

  it("filters by symbol without re-ranking", () => {
    render(<RankingBoard result={OK} />);
    fireEvent.change(screen.getByLabelText("Search symbol"), { target: { value: "btc" } });
    expect(symbols()).toEqual(["BTCUSDT"]);
    expect(screen.getByText("#3")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Search symbol"), { target: { value: "nope" } });
    expect(screen.getByText(/No symbol matches/)).toBeInTheDocument();
  });

  it("explains the model and each score", () => {
    render(<RankingBoard result={OK} />);
    expect(screen.getByText("Long — price reversal + falling Open Interest")).toBeInTheDocument();
    expect(screen.getByText(/linear_weighted_v1 · experimental/)).toBeInTheDocument();
    expect(screen.getByText(/Coins missing any of these features are excluded \(1: NEWUSDT\)/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Research behind this model →" })).toHaveAttribute("href", "/oink-corp/ranking");
    const row = screen.getAllByRole("row").find((r) => r.getAttribute("data-symbol") === "ZECUSDT")!;
    fireEvent.click(within(row).getByRole("button", { name: "Why?" }));
    const breakdown = screen.getByText("Contribution").closest("table")!;
    expect(within(breakdown).getByText("+0.900")).toBeInTheDocument();
    expect(within(breakdown).getByText("+0.400")).toBeInTheDocument();
    expect(within(breakdown).getAllByText("+1.300").length).toBe(1); // the total
  });

  it("states unavailable, stale and error conditions instead of fabricating rankings", () => {
    const { unmount } = render(
      <RankingBoard result={{ status: "ok", data: { available: false, reason: "Sniffer has not analyzed any frame yet.", frame: null, long: null, short: null } }} />,
    );
    expect(screen.getByText("Sniffer has not analyzed any frame yet.")).toBeInTheDocument();
    unmount();
    const missing = { ...DATA, short: { ...DATA.short!, available: false, reason: "candidate x not found", rows: [] } };
    const stale = { ...missing, frame: { ...DATA.frame!, stale: true, stale_reason: "the next frame should have finalized by 21:02" } };
    const second = render(<RankingBoard result={{ status: "ok", data: stale }} initialDirection="short" />);
    expect(screen.getByText(/Stale — the next frame should have finalized/)).toBeInTheDocument();
    expect(screen.getByText("The short ranking is unavailable.")).toBeInTheDocument();
    expect(screen.getByText("candidate x not found")).toBeInTheDocument();
    second.unmount();
    render(<RankingBoard result={{ status: "error", message: "HTTP 500" }} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Rankings could not be loaded.");
  });
});

describe("SnifferNav", () => {
  it("makes Rankings the default tab and keeps Market Data", () => {
    render(<SnifferNav />);
    expect(screen.getByRole("link", { name: "🍄 Rankings" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "📊 Market Data" })).toHaveAttribute("href", "/sniffer/market-data");
  });

  it("marks Market Data active on its page and neither on a symbol page", () => {
    pathname.value = "/sniffer/market-data";
    const { unmount } = render(<SnifferNav />);
    expect(screen.getByRole("link", { name: "📊 Market Data" })).toHaveAttribute("aria-current", "page");
    unmount();
    pathname.value = "/sniffer/BTCUSDT";
    render(<SnifferNav />);
    expect(screen.queryByRole("link", { current: "page" })).toBeNull();
  });
});

describe("RankingContext", () => {
  it("shows the coin's rank, score and breakdown in both directions", () => {
    render(<RankingContext symbol="BTCUSDT" rankings={OK} backDirection="short" />);
    const long = document.querySelector('[data-direction="long"]') as HTMLElement;
    expect(within(long).getByText("#3")).toBeInTheDocument();
    expect(long).toHaveTextContent("/ 795");
    const short = document.querySelector('[data-direction="short"]') as HTMLElement;
    expect(within(short).getByText("#1")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "← Back to short rankings" })).toHaveAttribute("href", "/sniffer?direction=short");
  });

  it("says why a coin is not ranked", () => {
    render(<RankingContext symbol="NEWUSDT" rankings={OK} backDirection="long" />);
    // Excluded by both models (the fixture shares the excluded list).
    expect(screen.getAllByText(/Not ranked — missing oi_change_1h in this frame/).length).toBe(2);
  });
});

const SPEC = {
  qualification_id: "long-reversal-oi-truffle-v1",
  name: "Long Truffle",
  spec_hash: "b".repeat(64),
  ranking_candidate: "long-price-reversal-oi-v1",
  direction: "long" as const,
  policy: "relative",
  description: "d",
  min_frame_completeness: 0.95,
  conditions: [
    { id: "return_tail", feature: "return_1h", measure: "percentile" as const, at_most: 0.05, at_least: null, rationale: "r" },
    { id: "oi_fell", feature: "oi_change_1h", measure: "value" as const, at_most: 0, at_least: null, rationale: "r" },
  ],
  status: "experimental",
};

function withTruffles(): LiveRankings {
  const long = DATA.long!;
  return {
    ...DATA,
    long: {
      ...long,
      // Rank 1 is NOT a Truffle; rank 2 is.
      rows: long.rows.map((r, i) => ({
        ...r,
        qualification:
          i === 1
            ? { qualified: true, failed: [], missing: [] }
            : { qualified: false, failed: ["return_tail"], missing: i === 2 ? ["oi_fell"] : [] },
      })),
      qualification: {
        status: "evaluated",
        reason: null,
        spec: SPEC,
        evaluated: 3,
        qualified: 1,
        truffles: [{ rank: 2, symbol: "ADAUSDT", score: 1.1 }],
      },
      diagnostics: {
        available: true,
        frame_time: "2026-10-24T20:55:00+00:00",
        candidate_id: "long-price-reversal-oi-v1",
        spec_hash: "a".repeat(64),
        temporal_contract_version: 2,
        limitations: ["descriptive"],
        eligible: 3,
        distribution: {
          min: 0.6,
          max: 1.3,
          mean: 1,
          median: 1.1,
          std: 0.29,
          spread: 0.7,
          percentiles: { p10: 0.7, p25: 0.85, p75: 1.2, p90: 1.26, p99: 1.3 },
          histogram: { low: 0.6, high: 1.3, counts: [1, 0, 2] },
        },
        separation: { k: 10, top1_minus_top2: 0.2, top1_minus_top10: null, top_k_mean_minus_rest_mean: null, near_top_fraction: 0.1, near_top_count: 1 },
        stability: { available: false, reason: "insufficient history: the previous frame has no Sniffer analysis", previous_frame_time: "2026-10-24T20:50:00+00:00", k: 10 },
      },
    },
    short: {
      ...DATA.short!,
      qualification: { status: "evaluated", reason: null, spec: { ...SPEC, direction: "short", qualification_id: "short-q" }, evaluated: 2, qualified: 0, truffles: [] },
    },
  };
}

describe("Experimental Truffles", () => {
  it("lists Truffles per direction, marks only qualified rows, and states zero naturally", () => {
    render(<RankingBoard result={{ status: "ok", data: withTruffles() }} />);
    const long = document.querySelector('[data-truffles="long"]') as HTMLElement;
    expect(within(long).getByRole("link", { name: /ADAUSDT/ })).toBeInTheDocument();
    expect(long).toHaveTextContent("1");
    const short = document.querySelector('[data-truffles="short"]') as HTMLElement;
    expect(within(short).getByText("No experimental Short Truffles qualified in this frame.")).toBeInTheDocument();
    const rows = screen.getAllByRole("row");
    const top = rows.find((r) => r.getAttribute("data-symbol") === "ZECUSDT")!;
    const ada = rows.find((r) => r.getAttribute("data-symbol") === "ADAUSDT")!;
    expect(within(top).queryByText(/Truffle/)).toBeNull(); // rank 1, not a Truffle
    expect(within(ada).getByText("🟢 Truffle")).toBeInTheDocument();
    expect(screen.getByText(/do not establish predictive validity/)).toBeInTheDocument();
  });

  it("explains why a row did not qualify, including missing inputs", () => {
    render(<RankingBoard result={{ status: "ok", data: withTruffles() }} />);
    const btc = screen.getAllByRole("row").find((r) => r.getAttribute("data-symbol") === "BTCUSDT")!;
    fireEvent.click(within(btc).getByRole("button", { name: "Why?" }));
    expect(screen.getByText("Not a Truffle")).toBeInTheDocument();
    expect(document.querySelector('[data-outcome="failed"]')).toHaveTextContent(/percentile ≤ 0.05/);
    expect(document.querySelector('[data-outcome="missing"]')).toHaveTextContent("input missing");
  });

  it("distinguishes withheld and unavailable qualification from zero Truffles", () => {
    const data = withTruffles();
    const withheld = {
      ...data,
      long: { ...data.long!, qualification: { ...data.long!.qualification!, status: "withheld" as const, reason: "stale ranking: late", truffles: [], qualified: null } },
      short: { ...data.short!, qualification: { status: "unavailable" as const, reason: "qualification x not found", spec: null, truffles: [] } },
    };
    render(<RankingBoard result={{ status: "ok", data: withheld }} />);
    expect(screen.getByText("Withheld — stale ranking: late")).toBeInTheDocument();
    expect(screen.getByText("Unavailable: qualification x not found")).toBeInTheDocument();
    expect(screen.queryByText(/No experimental .* Truffles qualified/)).toBeNull();
  });

  it("shows compact diagnostics, including insufficient stability history", () => {
    render(<RankingBoard result={{ status: "ok", data: withTruffles() }} />);
    const panel = screen.getByTestId("ranking-diagnostics");
    expect(panel).toHaveTextContent("Ranking diagnostics · 3 eligible");
    expect(panel).toHaveTextContent("+0.200"); // #1 − #2
    expect(panel).toHaveTextContent("insufficient history");
    expect(panel).toHaveTextContent("not expected profitability");
  });
});
