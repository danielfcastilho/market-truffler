import type {
  MarketStatus,
  ResearchAlert,
  ResearchStatus,
  SnifferStatus,
  SystemInfo,
} from "@/lib/server-api";

export type VitalStatus = "ok" | "down" | "unavailable";

export interface VitalRow {
  label: string;
  value: string;
  status: VitalStatus;
}

export interface VitalSection {
  title: string;
  rows: VitalRow[];
  /** Alerts shown under the rows (only OINK CORP has any). */
  alerts?: ResearchAlert[];
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
 * 5-minute boundaries of the last 24h with no Market Frame — collection
 * interruptions (host sleep, stack down). Live freshness recovers seconds
 * after a restart; this keeps the permanent gap visible for a day.
 */
function missedFramesRow(market: MarketStatus | null): VitalRow {
  const label = "Missed frames (24h)";
  if (market === null || market.frames_expected_24h === 0) {
    return unavailableRow(label);
  }
  const missed = market.frames_missed_24h;
  const last = market.last_missed_frame
    ? `, last ${formatTimestampUtc(market.last_missed_frame)}`
    : "";
  return {
    label,
    value: `${missed} of ${market.frames_expected_24h}${last}`,
    status: missed > 0 ? "down" : "ok",
  };
}

/**
 * Monthly partition upkeep. Lock contention on work that can wait
 * (next month's partition, retention) is retried automatically and is not
 * a failure; a missing current-month partition blocks ingestion and is.
 */
function partitionRow(market: MarketStatus | null): VitalRow {
  const label = "Partition maintenance";
  const maintenance = market?.partition_maintenance;
  if (!maintenance) return unavailableRow(label);
  const pending = maintenance.pending.length ? ` (${maintenance.pending.join(", ")})` : "";
  const cause = maintenance.last_error_category ?? "unknown error";
  switch (maintenance.state) {
    case "healthy":
      return {
        label,
        value: maintenance.last_success_at
          ? `Healthy (${formatTimestampUtc(maintenance.last_success_at)})`
          : "Healthy",
        status: "ok",
      };
    case "pending":
      return { label, value: "Not run yet", status: "ok" };
    case "retrying":
      return {
        label,
        value:
          `Deferred — ${cause}${pending}; retry ${maintenance.retry_count}` +
          (maintenance.next_retry_at ? ` at ${formatTimestampUtc(maintenance.next_retry_at)}` : ""),
        status: "ok",
      };
    case "deferred":
      return {
        label,
        value: `Deferred — ${cause}${pending}; retried at the next universe refresh`,
        status: "ok",
      };
    case "failed":
      return { label, value: `Failed — ${cause}${pending}`, status: "down" };
    case "required_unavailable":
      return {
        label,
        value: `Required partition unavailable — ingestion blocked (${cause})${pending}`,
        status: "down",
      };
  }
}

/**
 * macOS sleep protection, as reported every minute by the host's
 * infra/keep-awake.sh launchd agent. Proves only that macOS held the
 * agent's sleep-prevention assertion at the last report — not that the
 * host never slept, nor that collection is healthy (the frame rows say
 * that). A report older than the API's threshold is unknown, never active.
 */
function sleepProtectionRow(system: SystemInfo | null): VitalRow {
  const label = "Host sleep protection";
  const protection = system?.host_sleep_protection;
  if (!protection) return unavailableRow(label);
  const checked = protection.observed_at
    ? `checked ${formatTimestampUtc(protection.observed_at)}`
    : "never reported";
  switch (protection.state) {
    case "active":
      return {
        label,
        value:
          `Active — macOS assertion held by the keep-awake agent, ${checked}` +
          (protection.power_source && protection.power_source !== "AC Power"
            ? ` (on ${protection.power_source}: only idle sleep prevented)`
            : ""),
        status: "ok",
      };
    case "inactive":
      return {
        label,
        value: `Inactive — idle sleep will interrupt collection (${protection.detail}; ${checked}); run infra/keep-awake.sh start`,
        status: "down",
      };
    case "unknown":
      return {
        label,
        value: `Unknown — ${protection.detail}; check infra/keep-awake.sh status`,
        status: "unavailable",
      };
  }
}

/**
 * Published ranking snapshots: the newest captured one, and whether this
 * API process failed to capture any (never reported as captured).
 */
function snapshotRow(sniffer: SnifferStatus | null): VitalRow {
  const label = "Published snapshots";
  if (sniffer === null || sniffer.snapshot_capture === undefined) return unavailableRow(label);
  const capture = sniffer.snapshot_capture;
  if (capture && (capture.failures > 0 || capture.conflicts > 0)) {
    return {
      label,
      value: `${capture.failures} capture failure(s), ${capture.conflicts} conflict(s)${capture.last_error ? ` — ${capture.last_error}` : ""}`,
      status: "down",
    };
  }
  if (!sniffer.last_snapshot_published_at) return { label, value: "None captured yet", status: "unavailable" };
  return {
    label,
    value: `Last ${formatTimestampUtc(sniffer.last_snapshot_published_at)}`,
    status: "ok",
  };
}

/**
 * Finalized frames of the last 24h with no Sniffer analysis — an analysis
 * lost to a restart that startup recovery has not (yet) reached.
 */
function unanalyzedFramesRow(sniffer: SnifferStatus | null): VitalRow {
  const label = "Unanalyzed frames (24h)";
  const count = sniffer?.frames_unanalyzed_24h;
  if (sniffer === null || count == null) return unavailableRow(label);
  return { label, value: String(count), status: count > 0 ? "down" : "ok" };
}

/**
 * Prospective continuity alerts: problems that threaten a registered
 * evaluation's data (informational "awaiting data" entries are not counted).
 */
function continuityAlertsRow(research: ResearchStatus | null): VitalRow {
  const label = "Prospective continuity";
  if (research === null || research.alerts === undefined) return unavailableRow(label);
  const actionable = research.alerts.filter((a) => a.severity !== "info");
  const critical = actionable.filter((a) => a.severity === "critical").length;
  if (actionable.length === 0) {
    return { label, value: research.alerts_stale ? "Unknown — worker not reporting" : "No alerts", status: research.alerts_stale ? "unavailable" : "ok" };
  }
  return {
    label,
    value: `${actionable.length} alert(s)${critical ? `, ${critical} critical` : ""}`,
    status: "down",
  };
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
        sleepProtectionRow(system),
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
        missedFramesRow(market),
        partitionRow(market),
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
        unanalyzedFramesRow(sniffer),
        snapshotRow(sniffer),
      ],
    },
    {
      title: "🧬 OINK CORP",
      rows: [...researchRows(research ?? null), continuityAlertsRow(research ?? null)],
      alerts: (research?.alerts ?? []).filter((a) => a.severity !== "info"),
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

export function formatTimestampUtc(iso: string): string {
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
