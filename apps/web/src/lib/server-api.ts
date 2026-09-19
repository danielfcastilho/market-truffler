import { cookies } from "next/headers";

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
   * genuinely had no exact 5-minutes-ago candle to compare against —
   * never a substituted or zeroed value. */
  return_5m: string | null;
  /** Exact rolling 60-minute return using canonical 1m closes. */
  return_1h: string | null;
}

export interface SnifferFrame {
  frame_time: string;
  analyzed_at: string;
  instruments_analyzed: number;
  instruments: SnifferInstrument[];
}

export async function getLatestSnifferFrame(): Promise<SnifferFrame | null> {
  try {
    const response = await serverFetch("/api/sniffer/latest");
    if (!response.ok) return null;
    return (await response.json()) as SnifferFrame;
  } catch {
    return null;
  }
}
