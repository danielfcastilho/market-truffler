import { cookies } from "next/headers";
import type {
  CampaignDetail as CampaignDetailData,
  ExperimentDetail as ExperimentDetailData,
  RankingResearch as RankingResearchData,
  ResearchHistory as ResearchHistoryData,
  ResearchOverview as ResearchOverviewData,
} from "@/lib/research";
import type { LiveRankings as LiveRankingsData } from "@/lib/rankings";

const API_INTERNAL_URL = process.env.API_INTERNAL_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/**
 * Server-side fetch to the FastAPI backend, forwarding the visitor's session
 * cookie. Used from Server Components/layouts so protected routes are
 * enforced with a real backend check before any HTML is rendered.
 */
export async function serverFetch(path: string, init?: RequestInit): Promise<Response> {
  const cookieStore = await cookies();
  const cookieHeader = cookieStore.toString();

  return fetch(`${API_INTERNAL_URL}${path}`, {
    ...init,
    headers: {
      ...init?.headers,
      cookie: cookieHeader,
    },
    cache: "no-store",
  });
}

export interface CurrentUser {
  id: string;
  email: string;
  is_active: boolean;
  created_at: string;
}

export async function getCurrentUser(): Promise<CurrentUser | null> {
  try {
    const response = await serverFetch("/api/me");
    if (!response.ok) return null;
    return (await response.json()) as CurrentUser;
  } catch {
    return null;
  }
}

export interface SystemInfo {
  environment: string;
  version: string;
  database: "ok" | "unreachable";
  authenticated_user: string | null;
  uptime_seconds: number;
}

export async function getSystemInfo(): Promise<SystemInfo | null> {
  try {
    const response = await serverFetch("/api/system");
    if (!response.ok) return null;
    return (await response.json()) as SystemInfo;
  } catch {
    return null;
  }
}

export interface MarketStatus {
  bybit_connectivity: "ok" | "down";
  symbols_tracked: number | null;
  market_data: "ok" | "down";
  last_market_update: string | null;
  data_freshness_seconds: number | null;
  /** Fraction in [0, 1] of the active universe's promised rolling history
   * that is currently reconciled. Null when it can't be determined yet. */
  historical_coverage: number | null;
  /** The most recently finalized (COMPLETE or PARTIAL) Market Frame's
   * frame_time. Null when no frame has finalized yet. */
  latest_market_frame: string | null;
  /** That frame's available_instruments / expected_instruments, in [0, 1].
   * Null alongside latest_market_frame === null. */
  frame_completeness: number | null;
  frames_expected_24h: number;
  frames_missed_24h: number;
  last_missed_frame: string | null;
  /** Per-symbol historical-data readiness across the active universe right
   * now (DISCOVERED -> BACKFILLING -> READY — see the backend's
   * app.services.symbol_readiness). Always real counts summing to
   * symbols_tracked, never null: an empty universe is legitimately 0/0/0. */
  symbols_discovered: number;
  symbols_backfilling: number;
  symbols_ready: number;
}

export async function getMarketStatus(): Promise<MarketStatus | null> {
  try {
    const response = await serverFetch("/api/market/status");
    if (!response.ok) return null;
    return (await response.json()) as MarketStatus;
  } catch {
    return null;
  }
}

export interface SnifferStatus {
  status: "ok" | null;
  last_scan: string | null;
  coins_analyzed: number | null;
  /** Finalized frames of the last 24h Sniffer never analyzed (lost analyses). */
  frames_unanalyzed_24h?: number;
  /** "live" | "recovery" | null (before provenance was recorded). */
  last_analysis?: string | null;
}

/** OINK CORP research data continuity, as last reported by the backend's
 * `research` worker (apps/api/app/routers/research.py). Read-only. */
export interface ResearchEvent {
  day: string | null;
  at: string | null;
  kind: string | null;
  error: string | null;
}

export interface ResearchStatus {
  status: "healthy" | "recovering" | "degraded" | "blocked" | "unknown";
  reason: string;
  reported_at: string | null;
  heartbeat_at: string | null;
  generating_day: string | null;
  generating_since: string | null;
  last_preserved_day: string | null;
  preserved_days: number | null;
  missing_days: string[];
  waiting_days: number | null;
  due_days: number | null;
  failing_days: number | null;
  blocked_days: number | null;
  collection_gaps: number | null;
  unrecoverable_days: number | null;
  expiry_warnings: number | null;
  last_success: ResearchEvent | null;
  last_failure: ResearchEvent | null;
  last_interruption: ResearchEvent | null;
  datasets: number | null;
  unprotected_datasets: number | null;
  backup_configured: boolean;
  backup_status: "protected" | "unprotected" | "failing" | "not_configured" | "unknown";
  backup_checked_at: string | null;
  backup_error: string | null;
}

export async function getResearchStatus(): Promise<ResearchStatus | null> {
  try {
    const response = await serverFetch("/api/research/status");
    if (!response.ok) return null;
    return (await response.json()) as ResearchStatus;
  } catch {
    return null;
  }
}

export async function getSnifferStatus(): Promise<SnifferStatus | null> {
  try {
    const response = await serverFetch("/api/sniffer/status");
    if (!response.ok) return null;
    return (await response.json()) as SnifferStatus;
  } catch {
    return null;
  }
}

export interface SnifferInstrument {
  instrument_id: number;
  symbol: string;
  /** Decimal fraction (e.g. 0.01 == +1%) as a string, or null when Sniffer
   * genuinely had no exact historical candle to compare against — never a
   * substituted or zeroed value. Exact rolling return using canonical 5m
   * closes, one offset per labeled timeframe. */
  return_5m: string | null;
  return_15m: string | null;
  return_1h: string | null;
  return_4h: string | null;
  return_24h: string | null;
  /** Cutler's RSI(14) (see apps/api/app/features/rsi.py) over canonical
   * 5m/15m/1h/4h candles, anchored to this frame's already-selected
   * candle for that timeframe. A plain number (e.g. "63.42"), never a
   * percentage. Null when 15 consecutive closes weren't available.
   * Deliberately no rsi_14_24h — not part of this feature set yet. */
  rsi_14_5m: string | null;
  rsi_14_15m: string | null;
  rsi_14_1h: string | null;
  rsi_14_4h: string | null;
  /** Open Interest change: (current OI / OI `timeframe` ago) - 1, as the
   * same signed decimal-fraction convention as return_* (e.g. 0.05 ==
   * +5%) — never pre-multiplied by 100. Null when either OI observation
   * is unavailable (insufficient OI backfill, or a gap in Bybit's own
   * 5-minute OI series). */
  oi_change_5m: string | null;
  oi_change_15m: string | null;
  oi_change_1h: string | null;
  oi_change_4h: string | null;
  oi_change_24h: string | null;
  /** Volatility: ATR(14) divided by current_price (see
   * apps/api/app/features/volatility.py) — the same raw-fraction
   * convention as return_ and oi_change_ (0.05 == 5%), but always
   * non-negative: volatility has no direction, so this is never shown
   * with a +/- sign. Null when the 15-candle ATR window is unavailable.
   * Deliberately no volatility_5m — not part of this feature set yet. */
  volatility_15m: string | null;
  volatility_1h: string | null;
  volatility_4h: string | null;
  volatility_24h: string | null;
  /** Relative Volume: current-window volume divided by the mean of the
   * 14 preceding equivalent windows (see
   * apps/api/app/features/relative_volume.py) — a plain ratio, never a
   * percentage (1.0 == normal, 2.0 == double normal). Null when the
   * 15-candle window is unavailable, or the trailing baseline is zero.
   * Deliberately no relative_volume_24h — not part of this feature set
   * yet. */
  relative_volume_15m: string | null;
  relative_volume_1h: string | null;
  relative_volume_4h: string | null;
  /** Funding Rate: the latest settled perpetual funding observation
   * (funding_rate_current) and the plain mean of every observation in
   * the trailing 24h (funding_rate_24h_avg) — see
   * apps/api/app/features/funding_rate.py. Same raw-fraction convention
   * as return_ and oi_change_ (0.0001 == +0.01%), signed: a zero or
   * negative reading is a legitimate value, never treated as
   * unavailable. Null only when no funding observation exists yet in
   * the relevant window. */
  funding_rate_current: string | null;
  funding_rate_24h_avg: string | null;
}

export interface SnifferFrame {
  frame_time: string;
  analyzed_at: string;
  instruments_analyzed: number;
  instruments: SnifferInstrument[];
}

/** `symbol` narrows `instruments` to that coin (frame counts stay whole). */
export async function getLatestSnifferFrame(symbol?: string): Promise<SnifferFrame | null> {
  try {
    const query = symbol ? `?symbol=${encodeURIComponent(symbol)}` : "";
    const response = await serverFetch(`/api/sniffer/latest${query}`);
    if (!response.ok) return null;
    return (await response.json()) as SnifferFrame;
  } catch {
    return null;
  }
}

/**
 * OINK CORP Research Explorer reads (see `app/routers/research_explorer.py`).
 * A missing artifact (404) is distinguished from an unavailable API, so a
 * page can say "not found" without ever claiming research doesn't exist
 * when it merely couldn't be read.
 */
export type ResearchResult<T> =
  | { status: "ok"; data: T }
  | { status: "not_found" }
  | { status: "error"; message: string };

async function researchFetch<T>(path: string): Promise<ResearchResult<T>> {
  try {
    const response = await serverFetch(path);
    if (response.status === 404) return { status: "not_found" };
    if (!response.ok) return { status: "error", message: `HTTP ${response.status}` };
    return { status: "ok", data: (await response.json()) as T };
  } catch {
    return { status: "error", message: "research API unreachable" };
  }
}

export function getResearchOverview(): Promise<ResearchResult<ResearchOverviewData>> {
  return researchFetch("/api/research/overview");
}

export function getResearchCampaign(id: string): Promise<ResearchResult<CampaignDetailData>> {
  return researchFetch(`/api/research/campaigns/${encodeURIComponent(id)}`);
}

export function getResearchExperiment(id: string): Promise<ResearchResult<ExperimentDetailData>> {
  return researchFetch(`/api/research/experiments/${encodeURIComponent(id)}`);
}

export function getResearchHistory(): Promise<ResearchResult<ResearchHistoryData>> {
  return researchFetch("/api/research/history");
}

export function getRankingResearch(): Promise<ResearchResult<RankingResearchData>> {
  return researchFetch("/api/research/ranking");
}

export type LiveRankingsResult =
  | { status: "ok"; data: LiveRankingsData }
  | { status: "error"; message: string };

/** Experimental live Long/Short rankings — one snapshot, one frame. */
/** `symbol` keeps only that coin's row/exclusion, ranked over the whole frame. */
export async function getLiveRankings(symbol?: string): Promise<LiveRankingsResult> {
  try {
    const query = symbol ? `?symbol=${encodeURIComponent(symbol)}` : "";
    const response = await serverFetch(`/api/sniffer/rankings${query}`);
    if (!response.ok) return { status: "error", message: `HTTP ${response.status}` };
    return { status: "ok", data: (await response.json()) as LiveRankingsData };
  } catch {
    return { status: "error", message: "rankings API unreachable" };
  }
}
