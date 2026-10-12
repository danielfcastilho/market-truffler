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
  symbols_discovered: 2,
  symbols_backfilling: 10,
  symbols_ready: 202,
};

const healthySniffer: SnifferStatus = {
  status: "ok",
  last_scan: "2026-09-19T16:37:06.000Z",
  coins_analyzed: 771,
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
    expect(titles).toEqual(["SYSTEM", "MARKET", "🐽 SNIFFER", "🐗 WARHOG", "🧬 OINK CORP"]);
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

  it("reports rankings under OINK CORP (hardcoded N/A), never under Sniffer", () => {
    const sections = buildVitalsSections({
      health: "ok",
      ready: "ok",
      system: healthySystem,
      market: healthyMarket,
      sniffer: healthySniffer,
    });
    const sniffer = sections.find((s) => s.title === "🐽 SNIFFER");
    const oinkCorp = sections.find((s) => s.title === "🧬 OINK CORP");

    expect(sniffer?.rows.map((r) => r.label)).toEqual(["Status", "Last scan", "Coins analyzed"]);
    expect(oinkCorp?.rows.find((r) => r.label === "Latest ranking")).toEqual({
      label: "Latest ranking",
      value: "N/A",
      status: "unavailable",
    });
    expect(oinkCorp?.rows.find((r) => r.label === "🍄 Truffles found")).toEqual({
      label: "🍄 Truffles found",
      value: "N/A",
      status: "unavailable",
    });
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

describe("researchRows", () => {
  const base: ResearchStatus = {
    status: "ok",
    reported_at: "2026-10-20T12:00:00Z",
    generating_day: null,
    last_preserved_day: "2026-10-19",
    preserved_days: 3,
    waiting_days: 1,
    failing_days: 0,
    collection_gaps: 2,
    expiry_warnings: 0,
    unprotected_datasets: 4,
    backup_configured: false,
  };

  it("shows four N/A rows before the worker has ever reported", () => {
    const rows = researchRows(null);
    expect(rows.map((r) => r.label)).toEqual([
      "Research data",
      "Last preserved day",
      "Failing / waiting days",
      "Unprotected datasets",
    ]);
    expect(rows.every((r) => r.value === "N/A" && r.status === "unavailable")).toBe(true);
  });

  it("reports a healthy worker but flags datasets without an independent backup", () => {
    const rows = researchRows(base);
    expect(rows[0]).toEqual({ label: "Research data", value: "OK", status: "ok" });
    expect(rows[1]).toEqual({ label: "Last preserved day", value: "2026-10-19", status: "ok" });
    expect(rows[2]).toEqual({ label: "Failing / waiting days", value: "0 / 1", status: "ok" });
    expect(rows[3]).toEqual({
      label: "Unprotected datasets",
      value: "4 (no backup destination)",
      status: "down",
    });
  });

  it("flags failing days, a stale worker, and shows the day being generated", () => {
    const failing = researchRows({ ...base, status: "degraded", failing_days: 2 });
    expect(failing[0].status).toBe("down");
    expect(failing[2]).toEqual({ label: "Failing / waiting days", value: "2 / 1", status: "down" });
    const down = researchRows({ ...base, status: "down" });
    expect(down[0].value).toBe("Worker not reporting");
    const busy = researchRows({ ...base, generating_day: "2026-10-19" });
    expect(busy[0].value).toBe("OK (generating 2026-10-19)");
  });

  it("leads the OINK CORP section with the research-data rows", () => {
    const sections = buildVitalsSections({
      health: "ok",
      ready: "ok",
      system: null,
      market: null,
      sniffer: null,
      research: { ...base, status: "down" },
    });
    const oink = sections.find((s) => s.title === "🧬 OINK CORP");
    expect(oink?.rows.map((r) => r.label).slice(0, 4)).toEqual([
      "Research data",
      "Last preserved day",
      "Failing / waiting days",
      "Unprotected datasets",
    ]);
  });
});
