import { describe, expect, it } from "vitest";
import { buildVitalsSections, formatUptime, overallStatus, type VitalsInput } from "./vitals";
import type { SystemInfo } from "./server-api";

const healthySystem: SystemInfo = {
  environment: "development",
  version: "0.1.0",
  database: "ok",
  authenticated_user: "you@example.com",
  uptime_seconds: 125,
};

describe("overallStatus", () => {
  it("is ok when liveness, readiness, and the database are all healthy", () => {
    const input: VitalsInput = { health: "ok", ready: "ok", system: healthySystem };
    expect(overallStatus(input)).toBe("ok");
  });

  it("is down when liveness fails", () => {
    const input: VitalsInput = { health: "down", ready: "ok", system: healthySystem };
    expect(overallStatus(input)).toBe("down");
  });

  it("is down when the database is unreachable", () => {
    const input: VitalsInput = {
      health: "ok",
      ready: "down",
      system: { ...healthySystem, database: "unreachable" },
    };
    expect(overallStatus(input)).toBe("down");
  });

  it("is down when there is no system info at all (API unreachable)", () => {
    const input: VitalsInput = { health: "down", ready: "down", system: null };
    expect(overallStatus(input)).toBe("down");
  });
});

describe("buildVitalsSections", () => {
  it("reflects real signals in the SYSTEM section", () => {
    const sections = buildVitalsSections({ health: "ok", ready: "ok", system: healthySystem });
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
    });
    const system = sections.find((s) => s.title === "SYSTEM");
    expect(system?.rows.find((r) => r.label === "Database connectivity")).toEqual({
      label: "Database connectivity",
      value: "DOWN",
      status: "down",
    });
  });

  it("marks every SYSTEM row N/A when the API could not be reached at all", () => {
    const sections = buildVitalsSections({ health: "down", ready: "down", system: null });
    const system = sections.find((s) => s.title === "SYSTEM");
    expect(system?.rows.find((r) => r.label === "Uptime")?.value).toBe("N/A");
    expect(system?.rows.find((r) => r.label === "Environment")?.value).toBe("N/A");
    expect(system?.rows.find((r) => r.label === "Backend version")?.value).toBe("N/A");
  });

  it("includes a section per product area, laid out but not fabricated", () => {
    const sections = buildVitalsSections({ health: "ok", ready: "ok", system: healthySystem });
    const titles = sections.map((s) => s.title);
    expect(titles).toEqual(["SYSTEM", "MARKET", "🐽 SNIFFER", "🐗 WARHOG", "🧬 OINK CORP"]);
  });

  it("never fabricates a status for subsystems that do not exist yet", () => {
    const sections = buildVitalsSections({ health: "ok", ready: "ok", system: healthySystem });
    const placeholderSections = sections.filter((s) => s.title !== "SYSTEM");

    for (const section of placeholderSections) {
      for (const row of section.rows) {
        expect(row.status).toBe("unavailable");
        expect(row.value).toBe("N/A");
      }
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
