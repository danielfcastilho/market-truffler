import type { MarketStatus, ResearchStatus, SnifferStatus, SystemInfo } from "@/lib/server-api";

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
  sniffer: SnifferStatus | null;
  research?: ResearchStatus | null;
}

const NOT_AVAILABLE = "N/A";

function unavailableRow(label: string): VitalRow {
  return { label, value: NOT_AVAILABLE, status: "unavailable" };
}

const RESEARCH_STATUS_LABEL: Record<ResearchStatus["status"], string> = {
  healthy: "Healthy",
  recovering: "Recovering",
  degraded: "Degraded",
  blocked: "Blocked",
  unknown: "Unknown",
};

const RESEARCH_ROW_LABELS = [
  "Research continuity",
  "Last preserved day",
  "Missing days",
  "Last preservation",
  "Last failure",
  "Backup protection",
];

function backupRow(research: ResearchStatus): VitalRow {
  const label = "Backup protection";
  const total = research.datasets ?? 0;
  const unprotected = research.unprotected_datasets ?? 0;
  switch (research.backup_status) {
    case "protected": {
      const checked = research.backup_checked_at
        ? ` (${formatTimestampUtc(research.backup_checked_at)})`
        : "";
      return { label, value: `All ${total} verified${checked}`, status: "ok" };
    }
    case "failing":
      return { label, value: `Failing: ${research.backup_error ?? "copy errors"}`, status: "down" };
    case "unprotected":
      return { label, value: `${total - unprotected} of ${total} protected`, status: "down" };
    case "not_configured":
      return { label, value: `Not configured — 0 of ${total} protected`, status: "down" };
    default:
      return unavailableRow(label);
  }
}

/**
 * OINK CORP's research-data rows, from the read-only continuity report
 * (see `ResearchStatus`). Health is the backend's classification —
 * "Recovering" while recoverable days are due, never "Healthy" merely
 * because the worker runs. Backup protection is its own row and stays a
 * problem until an independent copy of every dataset has been verified.
 */
export function researchRows(research: ResearchStatus | null): VitalRow[] {
  if (research === null) {
    return RESEARCH_ROW_LABELS.map(unavailableRow);
  }
  const missing = research.missing_days ?? [];
  const failing = research.failing_days ?? 0;
  const shown = missing.slice(0, 3).join(", ") + (missing.length > 3 ? ", …" : "");
  const success = research.last_success;
  const failure = research.last_failure;
  return [
    {
      label: "Research continuity",
      value: `${RESEARCH_STATUS_LABEL[research.status]} — ${research.reason}`,
      status: research.status === "healthy" || research.status === "recovering" ? "ok" : "down",
    },
    {
      label: "Last preserved day",
      value: research.last_preserved_day ?? "None yet",
      status: research.last_preserved_day ? "ok" : missing.length > 0 ? "down" : "unavailable",
    },
    {
      label: "Missing days",
      value:
        `${missing.length} recoverable${missing.length ? ` (${shown})` : ""}` +
        ` · ${research.unrecoverable_days ?? 0} lost`,
      status: failing > 0 ? "down" : "ok",
    },
    {
      label: "Last preservation",
      value: success?.at ? `${success.day} at ${formatTimestampUtc(success.at)}` : "None yet",
      status: success?.at ? "ok" : "unavailable",
    },
    {
      label: "Last failure",
      value: failure?.at
        ? `${failure.day} · ${failure.kind ?? "unclassified"} at ${formatTimestampUtc(failure.at)}`
        : "None",
      status: failing > 0 ? "down" : "ok",
    },
    backupRow(research),
  ];
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
 * freshness" come from the MARKET WebSocket collector, "Historical
 * coverage" from the background history reconciler's persisted progress,
 * and "Latest market frame"/"Frame completeness" from the background frame
 * synchronizer's most recently finalized Market Frame (see
 * `app.services.frame_synchronizer` in the backend — a frame is a
 * synchronized, temporally-legal cross-section of the whole market,
 * produced once per closed UTC 5-minute boundary). All of it runs continuously in the
 * background; this page only observes it, never drives it — reading Vitals
 * never creates a frame. In SNIFFER, "Status"/"Last scan"/"Coins analyzed"
 * are likewise real, reflecting Sniffer's own persisted analysis of the
 * most recently finalized frame (see `app.services.sniffer`). OINK CORP
 * reports only what exists — its research-data continuity (see
 * `researchRows`). Vitals has no rows for capabilities that don't exist
 * yet (rankings, Truffles, experiment scheduling): a row is added when
 * there is a real signal behind it, never as a placeholder.
 */
export function buildVitalsSections({
  health,
  ready,
  system,
  market,
  sniffer,
  research,
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
          value:
            marketDataStatus === "unavailable" ? NOT_AVAILABLE : marketDataStatus.toUpperCase(),
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
          // How many tracked symbols currently have enough reconciled
          // history for every Sniff Sniffer computes today (DISCOVERED ->
          // BACKFILLING -> READY — see the backend's
          // app.services.symbol_readiness). Not a health signal (a low
          // number right after startup is expected, not "down") — status
          // just reflects whether the counts are known at all. Tied to
          // symbols_tracked's own nullability (the REST-probe-derived
          // "out of N" denominator), not just market's presence.
          label: "Symbols ready",
          value:
            market?.symbols_tracked != null
              ? `${market.symbols_ready} / ${market.symbols_tracked}`
              : NOT_AVAILABLE,
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
        {
          label: "Latest market frame",
          value: market?.latest_market_frame
            ? formatFrameTimeUtc(market.latest_market_frame)
            : NOT_AVAILABLE,
          status: market?.latest_market_frame ? "ok" : "unavailable",
        },
        {
          label: "Frame completeness",
          value:
            market?.frame_completeness != null
              ? formatCoveragePercent(market.frame_completeness)
              : NOT_AVAILABLE,
          status: market?.frame_completeness != null ? "ok" : "unavailable",
        },
      ],
    },
    {
      title: "🐽 SNIFFER",
      rows: [
        {
          label: "Status",
          value: sniffer?.status === "ok" ? "OK" : NOT_AVAILABLE,
          status: sniffer?.status === "ok" ? "ok" : "unavailable",
        },
        {
          label: "Last scan",
          value: sniffer?.last_scan ? formatTimestampUtc(sniffer.last_scan) : NOT_AVAILABLE,
          status: sniffer?.last_scan ? "ok" : "unavailable",
        },
        {
          label: "Coins analyzed",
          value: sniffer?.coins_analyzed != null ? String(sniffer.coins_analyzed) : NOT_AVAILABLE,
          status: sniffer?.coins_analyzed != null ? "ok" : "unavailable",
        },
      ],
    },
    {
      title: "🧬 OINK CORP",
      rows: researchRows(research ?? null),
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

/** Market Frames are always exactly on a UTC minute boundary, so
 * "HH:MM UTC" is unambiguous and matches how frame_time is described
 * throughout the backend (e.g. "FRAME 14:37"). */
function formatFrameTimeUtc(iso: string): string {
  const date = new Date(iso);
  const hh = String(date.getUTCHours()).padStart(2, "0");
  const mm = String(date.getUTCMinutes()).padStart(2, "0");
  return `${hh}:${mm} UTC`;
}

export function formatCoveragePercent(fraction: number): string {
  const percent = Math.max(0, Math.min(1, fraction)) * 100;
  const rounded = Math.round(percent * 10) / 10;
  return rounded >= 100 ? "100%" : `${rounded.toFixed(1)}%`;
}
