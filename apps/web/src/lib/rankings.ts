/**
 * Experimental live rankings (`GET /api/sniffer/rankings`): types and pure
 * display helpers. Scores and order come from the backend's shared scoring
 * core — nothing here recomputes or re-sorts them.
 */

import { FEATURE_COLUMNS } from "@/lib/sniffer-feature-columns";

export type RankDirection = "long" | "short";

export interface RankingContribution {
  feature: string;
  transform: "identity" | "cs_percentile" | "cs_robust_z";
  weight: number;
  raw: number;
  transformed: number;
  contribution: number;
}

export interface RankedCoin {
  rank: number;
  symbol: string;
  score: number;
  contributions: RankingContribution[];
  context: Record<string, number | null>;
}

export interface LiveModel {
  candidate_id: string;
  name: string;
  spec_hash: string;
  model_family: string;
  direction: RankDirection;
  objective: string;
  terms: { feature: string; transform: RankingContribution["transform"]; weight: number }[];
  missing_policy: string;
  validation_status: string;
}

export interface DirectionRanking {
  direction: RankDirection;
  available: boolean;
  reason: string | null;
  model: LiveModel | null;
  eligible: number | null;
  excluded: { symbol: string; missing: string[] }[];
  degenerate_features: string[];
  rows: RankedCoin[];
}

export interface LiveRankings {
  available: boolean;
  reason: string | null;
  frame: {
    frame_time: string;
    status: string;
    completeness: number;
    expected_instruments: number;
    available_instruments: number;
    analyzed_instruments: number;
    analyzed_at: string;
    age_seconds: number;
    stale: boolean;
    stale_reason: string | null;
  } | null;
  long: DirectionRanking | null;
  short: DirectionRanking | null;
}

const TRANSFORM_LABEL: Record<RankingContribution["transform"], string> = {
  identity: "raw value",
  cs_percentile: "percentile within the frame",
  cs_robust_z: "robust z-score within the frame",
};

export function transformLabel(transform: RankingContribution["transform"]): string {
  return TRANSFORM_LABEL[transform] ?? transform;
}

/** A feature's Market Data label, e.g. "Return 1h" — or its name. */
export function featureLabel(feature: string): string {
  return FEATURE_COLUMNS.find((c) => c.key === feature)?.label ?? feature;
}

/** A raw feature value formatted exactly like the Market Data table. */
export function formatFeature(feature: string, value: number | null | undefined): string {
  const column = FEATURE_COLUMNS.find((c) => c.key === feature);
  const text = value == null ? null : String(value);
  return column ? column.format(text) : (text ?? "—");
}

export function featureClassName(feature: string, value: number | null | undefined): string {
  const column = FEATURE_COLUMNS.find((c) => c.key === feature);
  return column ? column.valueClassName(value == null ? null : String(value)) : "";
}

export function formatScore(value: number): string {
  return `${value >= 0 ? "+" : "−"}${Math.abs(value).toFixed(3)}`;
}
