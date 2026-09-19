import type { MarketStatus, SystemInfo } from "@/lib/server-api";

export type VitalStatus = "ok" | "down" | "unavailable";

export interface VitalRow {
  label: string;
  value: string;
  status: VitalStatus;
}

export interface VitalSection {
  title: string;
  rows: VitalRow[];
}

export interface VitalsInput {
  health: "ok" | "down";
  ready: "ok" | "down";
  system: SystemInfo | null;
  market: MarketStatus | null;
}

const NOT_AVAILABLE = "N/A";

function unavailableRow(label: string): VitalRow {
  return { label, value: NOT_AVAILABLE, status: "unavailable" };
}

/**
 * Pure transform from raw probe results into the sections rendered by the
 * Vitals page. Kept separate from presentation (like `route-guard.ts`) so
 * the "what does the app honestly know about its own health" logic can be
 * unit tested without rendering anything.
 *
 * The SYSTEM section reflects real, currently-running infrastructure, and
 * every row in MARKET is real: "Bybit connectivity"/"Symbols tracked" come
 * from a REST call to Bybit, "Market data"/"Last market update"/"Data
 * freshness" come from the MARKET WebSocket collector, and "Historical
 * coverage" is a read-only snapshot of the background history reconciler's
 * persisted progress — the fraction of the active universe's promised
 * rolling history that is currently reconciled (see
 * `app.services.historical_coverage` in the backend for exact semantics).
 * All of it runs continuously in the background; this page only observes
 * it, never drives it. Sniffer/Warhog/OINK CORP lay out the shape Vitals
 * will eventually report on, but every row in them is a hardcoded "N/A":
 * there is no live signal for any of it yet, so none is invented.
 */
export function buildVitalsSections({
  health,
  ready,
  system,
  market,
}: VitalsInput): VitalSection[] {
  const databaseStatus: VitalStatus =
    system?.database === "ok" ? "ok" : system?.database === "unreachable" ? "down" : "unavailable";

  const bybitStatus: VitalStatus =
    market?.bybit_connectivity === "ok"
      ? "ok"
      : market?.bybit_connectivity === "down"
        ? "down"
        : "unavailable";

  const marketDataStatus: VitalStatus =
    market?.market_data === "ok" ? "ok" : market?.market_data === "down" ? "down" : "unavailable";

  return [
    {
      title: "SYSTEM",
      rows: [
        { label: "API liveness", value: health === "ok" ? "OK" : "DOWN", status: health },
        { label: "API readiness", value: ready === "ok" ? "OK" : "DOWN", status: ready },
        {
          label: "Database connectivity",
          value: databaseStatus === "unavailable" ? NOT_AVAILABLE : databaseStatus.toUpperCase(),
          status: databaseStatus,
        },
        {
          label: "Uptime",
          value: system ? formatUptime(system.uptime_seconds) : NOT_AVAILABLE,
          status: system ? "ok" : "unavailable",
        },
        {
          label: "Environment",
          value: system?.environment ?? NOT_AVAILABLE,
          status: system ? "ok" : "unavailable",
        },
        {
          label: "Backend version",
          value: system?.version ?? NOT_AVAILABLE,
          status: system ? "ok" : "unavailable",
        },
      ],
    },
    {
      title: "MARKET",
      rows: [
        {
          label: "Bybit connectivity",
          value: bybitStatus === "unavailable" ? NOT_AVAILABLE : bybitStatus.toUpperCase(),
          status: bybitStatus,
        },
        {
          label: "Market data",
          value: marketDataStatus === "unavailable" ? NOT_AVAILABLE : marketDataStatus.toUpperCase(),
          status: marketDataStatus,
        },
        {
          label: "Last market update",
          value: market?.last_market_update
            ? formatTimestampUtc(market.last_market_update)
            : NOT_AVAILABLE,
          status: market?.last_market_update ? "ok" : "unavailable",
        },
        {
          label: "Symbols tracked",
          value: market?.symbols_tracked != null ? String(market.symbols_tracked) : NOT_AVAILABLE,
          status: market?.symbols_tracked != null ? "ok" : "unavailable",
        },
        {
          label: "Data freshness",
          value:
            market?.data_freshness_seconds != null
              ? formatFreshness(market.data_freshness_seconds)
              : NOT_AVAILABLE,
          status: market?.data_freshness_seconds != null ? "ok" : "unavailable",
        },
        {
          label: "Historical coverage",
          value:
            market?.historical_coverage != null
              ? formatCoveragePercent(market.historical_coverage)
              : NOT_AVAILABLE,
          status: market?.historical_coverage != null ? "ok" : "unavailable",
        },
      ],
    },
    {
      title: "🐽 SNIFFER",
      rows: [
        unavailableRow("Status"),
        unavailableRow("Last scan"),
        unavailableRow("Coins analyzed"),
        unavailableRow("Latest ranking"),
        unavailableRow("🍄 Truffles found"),
      ],
    },
    {
      title: "🐗 WARHOG",
      rows: [
        unavailableRow("Status"),
        unavailableRow("Exchange"),
        unavailableRow("Open positions"),
        unavailableRow("Long exposure"),
        unavailableRow("Short exposure"),
        unavailableRow("Unrealized P&L"),
      ],
    },
    {
      title: "🧬 OINK CORP",
      rows: [
        unavailableRow("Status"),
        unavailableRow("Experiments running"),
        unavailableRow("Last experiment"),
      ],
    },
  ];
}

/**
 * Overall status is derived only from real signals (liveness, readiness,
 * database) — never from the placeholder "N/A" rows, which would otherwise
 * permanently pin the summary to "degraded".
 */
export function overallStatus({ health, ready, system }: VitalsInput): VitalStatus {
  if (health !== "ok") return "down";
  if (ready !== "ok") return "down";
  if (system?.database !== "ok") return "down";
  return "ok";
}

export function formatUptime(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;

  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

export function formatFreshness(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  if (seconds < 1) return "just now";
  return `${formatUptime(seconds)} ago`;
}

function formatTimestampUtc(iso: string): string {
  return `${new Date(iso).toISOString().slice(0, 19).replace("T", " ")} UTC`;
}

export function formatCoveragePercent(fraction: number): string {
  const percent = Math.max(0, Math.min(1, fraction)) * 100;
  const rounded = Math.round(percent * 10) / 10;
  return rounded >= 100 ? "100%" : `${rounded.toFixed(1)}%`;
}
