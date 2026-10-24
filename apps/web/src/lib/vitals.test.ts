import { describe, expect, it } from "vitest";
import type { ResearchStatus } from "@/lib/server-api";
import {
  buildVitalsSections,
  formatCoveragePercent,
  formatFreshness,
  formatUptime,
  overallStatus,
  researchRows,
  type VitalsInput,
} from "./vitals";
import type { MarketStatus, SnifferStatus, SystemInfo } from "./server-api";

const healthySystem: SystemInfo = {
  environment: "development",
  version: "0.1.0",
  database: "ok",
  authenticated_user: "you@example.com",
  uptime_seconds: 125,
};

const healthyMarket: MarketStatus = {
  bybit_connectivity: "ok",
  symbols_tracked: 214,
  market_data: "ok",
  last_market_update: "2026-09-19T16:42:07.000Z",
  data_freshness_seconds: 12,
  historical_coverage: 0.681,
  latest_market_frame: "2026-09-19T16:37:00.000Z",
  frame_completeness: 0.9987,
  frames_expected_24h: 288,
  frames_missed_24h: 0,
  last_missed_frame: null,
  symbols_discovered: 2,
  symbols_backfilling: 10,
  symbols_ready: 202,
};

const healthySniffer: SnifferStatus = {
  status: "ok",
  last_scan: "2026-09-19T16:37:06.000Z",
  coins_analyzed: 771,
  frames_unanalyzed_24h: 0,
};

const noSnifferScanYet: SnifferStatus = {
  status: null,
  last_scan: null,
  coins_analyzed: null,
};

const noMarketDataYet: MarketStatus = {
  bybit_connectivity: "ok",
  symbols_tracked: 214,
  market_data: "down",
  last_market_update: null,
  data_freshness_seconds: null,
  historical_coverage: 0.124,
  latest_market_frame: null,
  frame_completeness: null,
  frames_expected_24h: 0,
  frames_missed_24h: 0,
  last_missed_frame: null,
  symbols_discovered: 2,
  symbols_backfilling: 10,
  symbols_ready: 202,
};

describe("overallStatus", () => {
  it("is ok when liveness, readiness, and the database are all healthy", () => {
    const input: VitalsInput = {
      health: "ok",
      ready: "ok",
      system: healthySystem,
      market: healthyMarket,
      sniffer: healthySniffer,
    };
    expect(overallStatus(input)).toBe("ok");
  });

  it("is down when liveness fails", () => {
    const input: VitalsInput = {
      health: "down",
      ready: "ok",
      system: healthySystem,
      market: healthyMarket,
      sniffer: healthySniffer,
    };
    expect(overallStatus(input)).toBe("down");
  });

  it("is down when the database is unreachable", () => {
    const input: VitalsInput = {
      health: "ok",
      ready: "down",
      system: { ...healthySystem, database: "unreachable" },
      market: healthyMarket,
      sniffer: healthySniffer,
    };
    expect(overallStatus(input)).toBe("down");
  });

  it("is down when there is no system info at all (API unreachable)", () => {
    const input: VitalsInput = {
      health: "down",
      ready: "down",
      system: null,
      market: null,
      sniffer: null,
    };
    expect(overallStatus(input)).toBe("down");
  });

  it("stays ok even when Bybit is unreachable (an external dependency, not core health)", () => {
    const input: VitalsInput = {
      health: "ok",
      ready: "ok",
      system: healthySystem,
      market: {
        bybit_connectivity: "down",
        symbols_tracked: null,
        market_data: "down",
        last_market_update: null,
        data_freshness_seconds: null,
        historical_coverage: null,
        latest_market_frame: null,
        frame_completeness: null,
        frames_expected_24h: 0,
        frames_missed_24h: 0,
        last_missed_frame: null,
        symbols_discovered: 0,
        symbols_backfilling: 0,
        symbols_ready: 0,
      },
      sniffer: healthySniffer,
    };
    expect(overallStatus(input)).toBe("ok");
  });
});

describe("buildVitalsSections", () => {
  it("reflects real signals in the SYSTEM section", () => {
    const sections = buildVitalsSections({
      health: "ok",
      ready: "ok",
      system: healthySystem,
      market: healthyMarket,
      sniffer: healthySniffer,
    });
    const system = sections.find((s) => s.title === "SYSTEM");
    expect(system?.rows.find((r) => r.label === "API liveness")).toEqual({
      label: "API liveness",
      value: "OK",
      status: "ok",
    });
    expect(system?.rows.find((r) => r.label === "Database connectivity")?.status).toBe("ok");
    expect(system?.rows.find((r) => r.label === "Uptime")?.value).toBe("2m 5s");
    expect(system?.rows.find((r) => r.label === "Environment")?.value).toBe("development");
    expect(system?.rows.find((r) => r.label === "Backend version")?.value).toBe("0.1.0");
  });

  it("marks database connectivity down when reported unreachable", () => {
    const sections = buildVitalsSections({
      health: "ok",
      ready: "ok",
      system: { ...healthySystem, database: "unreachable" },
      market: healthyMarket,
      sniffer: healthySniffer,
    });
    const system = sections.find((s) => s.title === "SYSTEM");
    expect(system?.rows.find((r) => r.label === "Database connectivity")).toEqual({
      label: "Database connectivity",
      value: "DOWN",
      status: "down",
    });
  });

  it("marks every SYSTEM row N/A when the API could not be reached at all", () => {
    const sections = buildVitalsSections({
      health: "down",
      ready: "down",
      system: null,
      market: null,
      sniffer: null,
    });
    const system = sections.find((s) => s.title === "SYSTEM");
    expect(system?.rows.find((r) => r.label === "Uptime")?.value).toBe("N/A");
    expect(system?.rows.find((r) => r.label === "Environment")?.value).toBe("N/A");
    expect(system?.rows.find((r) => r.label === "Backend version")?.value).toBe("N/A");
  });

  it("includes a section per product area, laid out but not fabricated", () => {
    const sections = buildVitalsSections({
      health: "ok",
      ready: "ok",
      system: healthySystem,
      market: healthyMarket,
      sniffer: healthySniffer,
    });
    const titles = sections.map((s) => s.title);
    expect(titles).toEqual(["SYSTEM", "MARKET", "🐽 SNIFFER", "🧬 OINK CORP"]);
  });

  it("never fabricates a status for subsystems that do not exist yet", () => {
    const sections = buildVitalsSections({
      health: "ok",
      ready: "ok",
      system: healthySystem,
      market: healthyMarket,
      sniffer: null,
    });
    const placeholderSections = sections.filter(
      (s) => s.title !== "SYSTEM" && s.title !== "MARKET",
    );

    for (const section of placeholderSections) {
      for (const row of section.rows) {
        expect(row.status).toBe("unavailable");
        expect(row.value).toBe("N/A");
      }
    }
  });

  it("reflects a fully live MARKET section: Bybit connectivity, collector state, and freshness", () => {
    const sections = buildVitalsSections({
      health: "ok",
      ready: "ok",
      system: healthySystem,
      market: healthyMarket,
      sniffer: healthySniffer,
    });
    const market = sections.find((s) => s.title === "MARKET");

    expect(market?.rows.find((r) => r.label === "Bybit connectivity")).toEqual({
      label: "Bybit connectivity",
      value: "OK",
      status: "ok",
    });
    expect(market?.rows.find((r) => r.label === "Market data")).toEqual({
      label: "Market data",
      value: "OK",
      status: "ok",
    });
    expect(market?.rows.find((r) => r.label === "Last market update")).toEqual({
      label: "Last market update",
      value: "2026-09-19 16:42:07 UTC",
      status: "ok",
    });
    expect(market?.rows.find((r) => r.label === "Symbols tracked")).toEqual({
      label: "Symbols tracked",
      value: "214",
      status: "ok",
    });
    expect(market?.rows.find((r) => r.label === "Symbols ready")).toEqual({
      label: "Symbols ready",
      value: "202 / 214",
      status: "ok",
    });
    expect(market?.rows.find((r) => r.label === "Data freshness")).toEqual({
      label: "Data freshness",
      value: "12s ago",
      status: "ok",
    });
    expect(market?.rows.find((r) => r.label === "Historical coverage")).toEqual({
      label: "Historical coverage",
      value: "68.1%",
      status: "ok",
    });
    expect(market?.rows.find((r) => r.label === "Latest market frame")).toEqual({
      label: "Latest market frame",
      value: "16:37 UTC",
      status: "ok",
    });
    expect(market?.rows.find((r) => r.label === "Frame completeness")).toEqual({
      label: "Frame completeness",
      value: "99.9%",
      status: "ok",
    });
    expect(market?.rows.find((r) => r.label === "Missed frames (24h)")).toEqual({
      label: "Missed frames (24h)",
      value: "0 of 288",
      status: "ok",
    });
  });

  it("keeps a collection interruption visible after collection resumes", () => {
    const sections = buildVitalsSections({
      health: "ok",
      ready: "ok",
      system: healthySystem,
      market: {
        ...healthyMarket,
        frames_missed_24h: 24,
        last_missed_frame: "2026-09-19T04:55:00.000Z",
      },
      sniffer: healthySniffer,
    });
    const market = sections.find((s) => s.title === "MARKET");
    expect(market?.rows.find((r) => r.label === "Missed frames (24h)")).toEqual({
      label: "Missed frames (24h)",
      value: "24 of 288, last 2026-09-19 04:55:00 UTC",
      status: "down",
    });
    const fresh = buildVitalsSections({
      health: "ok",
      ready: "ok",
      system: healthySystem,
      market: noMarketDataYet,
      sniffer: healthySniffer,
    });
    expect(
      fresh.find((s) => s.title === "MARKET")?.rows.find((r) => r.label === "Missed frames (24h)"),
    ).toEqual({ label: "Missed frames (24h)", value: "N/A", status: "unavailable" });
  });

  it("reports Bybit connectivity as down and symbols tracked as N/A when Bybit REST is unreachable", () => {
    const sections = buildVitalsSections({
      health: "ok",
      ready: "ok",
      system: healthySystem,
      market: {
        bybit_connectivity: "down",
        symbols_tracked: null,
        market_data: "down",
        last_market_update: null,
        data_freshness_seconds: null,
        historical_coverage: null,
        latest_market_frame: null,
        frame_completeness: null,
        frames_expected_24h: 0,
        frames_missed_24h: 0,
        last_missed_frame: null,
        symbols_discovered: 0,
        symbols_backfilling: 0,
        symbols_ready: 0,
      },
      sniffer: healthySniffer,
    });
    const market = sections.find((s) => s.title === "MARKET");

    expect(market?.rows.find((r) => r.label === "Bybit connectivity")).toEqual({
      label: "Bybit connectivity",
      value: "DOWN",
      status: "down",
    });
    expect(market?.rows.find((r) => r.label === "Symbols tracked")).toEqual({
      label: "Symbols tracked",
      value: "N/A",
      status: "unavailable",
    });
    // Readiness counts are meaningless without a known "out of N" —
    // stays N/A even though symbols_ready/backfilling/discovered
    // themselves are technically real zeros in this fixture.
    expect(market?.rows.find((r) => r.label === "Symbols ready")).toEqual({
      label: "Symbols ready",
      value: "N/A",
      status: "unavailable",
    });
  });

  it("reports market data as down and freshness as N/A when the collector has no candle yet", () => {
    const sections = buildVitalsSections({
      health: "ok",
      ready: "ok",
      system: healthySystem,
      market: noMarketDataYet,
      sniffer: healthySniffer,
    });
    const market = sections.find((s) => s.title === "MARKET");

    expect(market?.rows.find((r) => r.label === "Market data")).toEqual({
      label: "Market data",
      value: "DOWN",
      status: "down",
    });
    expect(market?.rows.find((r) => r.label === "Last market update")).toEqual({
      label: "Last market update",
      value: "N/A",
      status: "unavailable",
    });
    expect(market?.rows.find((r) => r.label === "Data freshness")).toEqual({
      label: "Data freshness",
      value: "N/A",
      status: "unavailable",
    });
    // Symbols tracked is a separate, unrelated signal (REST universe size) —
    // it must stay real even while the WS collector has no data yet.
    expect(market?.rows.find((r) => r.label === "Symbols tracked")?.value).toBe("214");
    // Historical coverage is independent of live WS state too — it reflects
    // the background reconciler's own persisted progress.
    expect(market?.rows.find((r) => r.label === "Historical coverage")).toEqual({
      label: "Historical coverage",
      value: "12.4%",
      status: "ok",
    });
    // No frame has finalized yet — must read N/A, never a fabricated value.
    expect(market?.rows.find((r) => r.label === "Latest market frame")).toEqual({
      label: "Latest market frame",
      value: "N/A",
      status: "unavailable",
    });
    expect(market?.rows.find((r) => r.label === "Frame completeness")).toEqual({
      label: "Frame completeness",
      value: "N/A",
      status: "unavailable",
    });
  });

  it("marks every MARKET row N/A when market status could not be fetched at all", () => {
    const sections = buildVitalsSections({
      health: "ok",
      ready: "ok",
      system: healthySystem,
      market: null,
      sniffer: healthySniffer,
    });
    const market = sections.find((s) => s.title === "MARKET");

    for (const row of market?.rows ?? []) {
      expect(row.value).toBe("N/A");
      expect(row.status).toBe("unavailable");
    }
  });

  it("reflects a real SNIFFER status/last-scan/coins-analyzed once Sniffer has analyzed a frame", () => {
    const sections = buildVitalsSections({
      health: "ok",
      ready: "ok",
      system: healthySystem,
      market: healthyMarket,
      sniffer: healthySniffer,
    });
    const sniffer = sections.find((s) => s.title === "🐽 SNIFFER");

    expect(sniffer?.rows.find((r) => r.label === "Status")).toEqual({
      label: "Status",
      value: "OK",
      status: "ok",
    });
    expect(sniffer?.rows.find((r) => r.label === "Last scan")).toEqual({
      label: "Last scan",
      value: "2026-09-19 16:37:06 UTC",
      status: "ok",
    });
    expect(sniffer?.rows.find((r) => r.label === "Coins analyzed")).toEqual({
      label: "Coins analyzed",
      value: "771",
      status: "ok",
    });
  });

  it("keeps frames whose Sniffer analysis was lost visible", () => {
    const rows = (unanalyzed: number) =>
      buildVitalsSections({
        health: "ok",
        ready: "ok",
        system: healthySystem,
        market: healthyMarket,
        sniffer: { ...healthySniffer, frames_unanalyzed_24h: unanalyzed },
      }).find((s) => s.title === "🐽 SNIFFER")?.rows;
    expect(rows(0)?.find((r) => r.label === "Unanalyzed frames (24h)")).toEqual({
      label: "Unanalyzed frames (24h)",
      value: "0",
      status: "ok",
    });
    expect(rows(3)?.find((r) => r.label === "Unanalyzed frames (24h)")?.status).toBe("down");
  });

  it("has no placeholder rows for capabilities that do not exist yet", () => {
    const sections = buildVitalsSections({
      health: "ok",
      ready: "ok",
      system: healthySystem,
      market: healthyMarket,
      sniffer: healthySniffer,
    });
    const sniffer = sections.find((s) => s.title === "🐽 SNIFFER");
    const oinkCorp = sections.find((s) => s.title === "🧬 OINK CORP");

    expect(sniffer?.rows.map((r) => r.label)).toEqual([
      "Status",
      "Last scan",
      "Coins analyzed",
      "Unanalyzed frames (24h)",
    ]);
    // OINK CORP shows only its real research-data signal — no ranking,
    // Truffle, or experiment rows until those capabilities exist.
    expect(oinkCorp?.rows.map((r) => r.label)).toEqual(RESEARCH_LABELS);
    const labels = sections.flatMap((s) => s.rows.map((r) => r.label.toLowerCase()));
    for (const absent of ["ranking", "truffle", "position", "exposure", "p&l", "exchange"]) {
      expect(labels.some((l) => l.includes(absent))).toBe(false);
    }
  });

  it("marks every SNIFFER row N/A when Sniffer has never analyzed a frame", () => {
    const sections = buildVitalsSections({
      health: "ok",
      ready: "ok",
      system: healthySystem,
      market: healthyMarket,
      sniffer: noSnifferScanYet,
    });
    const sniffer = sections.find((s) => s.title === "🐽 SNIFFER");

    for (const row of sniffer?.rows ?? []) {
      expect(row.value).toBe("N/A");
      expect(row.status).toBe("unavailable");
    }
  });
});

describe("formatUptime", () => {
  it("formats seconds only", () => {
    expect(formatUptime(45)).toBe("45s");
  });

  it("formats minutes and seconds", () => {
    expect(formatUptime(125)).toBe("2m 5s");
  });

  it("formats hours and minutes", () => {
    expect(formatUptime(3 * 3600 + 90)).toBe("3h 1m");
  });

  it("formats days and hours", () => {
    expect(formatUptime(2 * 86400 + 5 * 3600)).toBe("2d 5h");
  });

  it("never goes negative", () => {
    expect(formatUptime(-5)).toBe("0s");
  });
});

describe("formatCoveragePercent", () => {
  it("formats a fraction as a one-decimal percentage", () => {
    expect(formatCoveragePercent(0.124)).toBe("12.4%");
    expect(formatCoveragePercent(0.681)).toBe("68.1%");
  });

  it("shows a clean 100% instead of 100.0%", () => {
    expect(formatCoveragePercent(1)).toBe("100%");
  });

  it("rounds values that are effectively complete up to 100%", () => {
    expect(formatCoveragePercent(0.9999999944)).toBe("100%");
  });

  it("clamps out-of-range fractions", () => {
    expect(formatCoveragePercent(-0.1)).toBe("0.0%");
    expect(formatCoveragePercent(1.5)).toBe("100%");
  });
});

describe("formatFreshness", () => {
  it("reports sub-second freshness as just now", () => {
    expect(formatFreshness(0.4)).toBe("just now");
  });

  it("reports elapsed time with 'ago'", () => {
    expect(formatFreshness(12)).toBe("12s ago");
    expect(formatFreshness(125)).toBe("2m 5s ago");
  });

  it("never goes negative", () => {
    expect(formatFreshness(-3)).toBe("just now");
  });
});

it("renders the backend coverage fraction exactly once as a percentage", () => {
  const sections = buildVitalsSections({
    health: "ok",
    ready: "ok",
    system: healthySystem,
    market: { ...healthyMarket, historical_coverage: 0.207 },
    sniffer: healthySniffer,
  });
  expect(
    sections.flatMap((section) => section.rows).find((row) => row.label === "Historical coverage")
      ?.value,
  ).toBe("20.7%");
});

const RESEARCH_LABELS = [
  "Research continuity",
  "Last preserved day",
  "Missing days",
  "Last preservation",
  "Last failure",
  "Backup protection",
];

describe("researchRows", () => {
  const base: ResearchStatus = {
    status: "healthy",
    reason: "all recoverable days preserved or waiting normally",
    reported_at: "2026-10-20T12:00:00Z",
    heartbeat_at: "2026-10-20T12:00:00Z",
    generating_day: null,
    generating_since: null,
    last_preserved_day: "2026-10-19",
    preserved_days: 3,
    missing_days: [],
    waiting_days: 1,
    due_days: 0,
    failing_days: 0,
    blocked_days: 0,
    collection_gaps: 2,
    unrecoverable_days: 2,
    expiry_warnings: 0,
    last_success: { day: "2026-10-19", at: "2026-10-20T06:10:00Z", kind: null, error: null },
    last_failure: null,
    last_interruption: null,
    datasets: 4,
    unprotected_datasets: 4,
    backup_configured: false,
    backup_status: "not_configured",
    backup_checked_at: null,
    backup_error: null,
  };

  it("shows N/A rows when the status could not be fetched", () => {
    const rows = researchRows(null);
    expect(rows.map((r) => r.label)).toEqual(RESEARCH_LABELS);
    expect(rows.every((r) => r.value === "N/A" && r.status === "unavailable")).toBe(true);
  });

  it("reports healthy continuity but flags an unconfigured backup", () => {
    const rows = researchRows(base);
    expect(rows[0]).toEqual({
      label: "Research continuity",
      value: "Healthy — all recoverable days preserved or waiting normally",
      status: "ok",
    });
    expect(rows[1]).toEqual({ label: "Last preserved day", value: "2026-10-19", status: "ok" });
    expect(rows[2]).toEqual({ label: "Missing days", value: "0 recoverable · 2 lost", status: "ok" });
    expect(rows[3].value).toBe("2026-10-19 at 2026-10-20 06:10:00 UTC");
    expect(rows[4]).toEqual({ label: "Last failure", value: "None", status: "ok" });
    expect(rows[5]).toEqual({
      label: "Backup protection",
      value: "Not configured — 0 of 4 protected",
      status: "down",
    });
  });

  it("never shows nothing-preserved-yet as healthy", () => {
    const rows = researchRows({
      ...base,
      status: "recovering",
      reason: "generating 2026-10-12",
      generating_day: "2026-10-12",
      last_preserved_day: null,
      preserved_days: 0,
      missing_days: ["2026-10-12", "2026-10-13"],
      due_days: 2,
      last_success: null,
    });
    expect(rows[0].value).toBe("Recovering — generating 2026-10-12");
    expect(rows[1]).toEqual({ label: "Last preserved day", value: "None yet", status: "down" });
    expect(rows[2].value).toBe("2 recoverable (2026-10-12, 2026-10-13) · 2 lost");
    expect(rows[3]).toEqual({ label: "Last preservation", value: "None yet", status: "unavailable" });
  });

  it("flags failures by kind, blocked or unknown continuity", () => {
    const failing = researchRows({
      ...base,
      status: "degraded",
      reason: "1 day(s) failing (timeout), retrying",
      failing_days: 1,
      missing_days: ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"],
      last_failure: { day: "2026-10-19", at: "2026-10-20T08:00:00Z", kind: "timeout", error: "x" },
    });
    expect(failing[0].status).toBe("down");
    expect(failing[2]).toEqual({
      label: "Missing days",
      value: "4 recoverable (2026-10-19, 2026-10-20, 2026-10-21, …) · 2 lost",
      status: "down",
    });
    expect(failing[4]).toEqual({
      label: "Last failure",
      value: "2026-10-19 · timeout at 2026-10-20 08:00:00 UTC",
      status: "down",
    });
    expect(researchRows({ ...base, status: "blocked", reason: "r" })[0].status).toBe("down");
    const unknown = researchRows({ ...base, status: "unknown", reason: "worker not reporting" });
    expect(unknown[0]).toEqual({
      label: "Research continuity",
      value: "Unknown — worker not reporting",
      status: "down",
    });
  });

  it("shows backup protection only as verified by the last backup run", () => {
    const protectedRows = researchRows({
      ...base,
      backup_configured: true,
      backup_status: "protected",
      unprotected_datasets: 0,
      backup_checked_at: "2026-10-20T12:30:00Z",
    });
    expect(protectedRows[5]).toEqual({
      label: "Backup protection",
      value: "All 4 verified (2026-10-20 12:30:00 UTC)",
      status: "ok",
    });
    const partial = researchRows({ ...base, backup_status: "unprotected", unprotected_datasets: 1 });
    expect(partial[5]).toEqual({
      label: "Backup protection",
      value: "3 of 4 protected",
      status: "down",
    });
    const failing = researchRows({ ...base, backup_status: "failing", backup_error: "not mounted" });
    expect(failing[5]).toEqual({
      label: "Backup protection",
      value: "Failing: not mounted",
      status: "down",
    });
  });

  it("leads the OINK CORP section with the research-data rows", () => {
    const sections = buildVitalsSections({
      health: "ok",
      ready: "ok",
      system: null,
      market: null,
      sniffer: null,
      research: { ...base, status: "unknown", reason: "worker not reporting" },
    });
    const oink = sections.find((s) => s.title === "🧬 OINK CORP");
    expect(oink?.rows.map((r) => r.label)).toEqual(RESEARCH_LABELS);
  });
});
