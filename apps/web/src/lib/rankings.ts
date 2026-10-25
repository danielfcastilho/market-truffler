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

export type CheckOutcome = "passed" | "failed" | "missing";

export interface QualificationCheck {
  id: string;
  feature: string;
  measure: "percentile" | "value";
  threshold: { at_most?: number; at_least?: number };
  observed: number | null;
  outcome: CheckOutcome;
}

/** A row's qualification against the direction's spec; null when withheld. */
export interface RowQualification {
  qualified: boolean;
  failed: string[];
  missing: string[];
  /** Only on a symbol slice (`?symbol=`). */
  checks?: QualificationCheck[];
}

export interface RankedCoin {
  rank: number;
  symbol: string;
  score: number;
  contributions: RankingContribution[];
  context: Record<string, number | null>;
  qualification?: RowQualification | null;
}

export interface QualificationCondition {
  id: string;
  feature: string;
  measure: "percentile" | "value";
  at_most: number | null;
  at_least: number | null;
  rationale: string;
}

export interface QualificationSpec {
  qualification_id: string;
  name: string;
  spec_hash: string;
  ranking_candidate: string;
  direction: RankDirection;
  policy: string;
  description: string;
  min_frame_completeness: number;
  conditions: QualificationCondition[];
  status: string;
}

export interface DirectionQualification {
  /** evaluated (possibly zero Truffles) · withheld (stale/incomplete frame) · unavailable */
  status: "evaluated" | "withheld" | "unavailable";
  reason: string | null;
  spec: QualificationSpec | null;
  evaluated?: number;
  qualified?: number | null;
  truffles: { rank: number; symbol: string; score: number }[];
  frame_checks?: { id: string; outcome: CheckOutcome; observed: unknown }[];
}

export interface RankingDiagnostics {
  available: boolean;
  reason?: string;
  frame_time: string;
  candidate_id: string | null;
  spec_hash: string | null;
  temporal_contract_version: number;
  limitations: string[];
  eligible?: number;
  distribution?: {
    min: number;
    max: number;
    mean: number;
    median: number;
    std: number;
    spread: number;
    percentiles: Record<string, number>;
    histogram: { low: number; high: number; counts: number[] };
  };
  separation?: {
    k: number;
    top1_minus_top2: number | null;
    top1_minus_top10: number | null;
    top_k_mean_minus_rest_mean: number | null;
    near_top_fraction: number;
    near_top_count: number;
  };
  stability?: {
    available: boolean;
    reason?: string;
    previous_frame_time: string;
    k: number;
    top_k_overlap?: number;
    entered_top_k?: string[];
    left_top_k?: string[];
    rank_correlation?: number | null;
  };
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
  diagnostics?: RankingDiagnostics | null;
  qualification?: DirectionQualification | null;
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
    /** "live" | "recovery" | null (before analysis provenance was recorded). */
    analysis?: string | null;
    age_seconds: number;
    stale: boolean;
    stale_reason: string | null;
  } | null;
  long: DirectionRanking | null;
  short: DirectionRanking | null;
  truffles_in_both_directions?: string[];
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

/** A condition as text, e.g. "Return 1h percentile ≤ 0.05". */
export function conditionLabel(c: {
  feature: string;
  measure: "percentile" | "value";
  at_most?: number | null;
  at_least?: number | null;
}): string {
  const bound = c.at_most != null ? `≤ ${c.at_most}` : `≥ ${c.at_least}`;
  return `${featureLabel(c.feature)} ${c.measure === "percentile" ? "percentile" : "value"} ${bound}`;
}

export function truffleEmoji(direction: RankDirection): string {
  return direction === "long" ? "🟢" : "🔴";
}


// -- published ranking snapshots (`/api/sniffer/rankings/history`) ----------------------------

export interface RankingSnapshotSummary {
  id: number;
  frame_time: string;
  analyzed_at: string;
  analysis: string | null;
  published_at: string;
  temporal_contract_version: number | null;
  long_candidate: string | null;
  long_candidate_hash: string | null;
  short_candidate: string | null;
  short_candidate_hash: string | null;
  long_qualification: string | null;
  long_qualification_hash: string | null;
  short_qualification: string | null;
  short_qualification_hash: string | null;
  long_qualification_status: string | null;
  short_qualification_status: string | null;
  long_truffles: number | null;
  short_truffles: number | null;
  schema_version: number;
  payload_sha256: string;
}

export interface RankingHistoryPage {
  items: RankingSnapshotSummary[];
  next_before: number | null;
}

export interface Movement {
  rank: number;
  previous_rank: number | null;
  change: number | null;
}

export type DirectionComparison =
  | { available: false; reason: string }
  | {
      available: true;
      k: number;
      model_changed: boolean;
      policy_changed: boolean;
      entered_top_k: string[];
      left_top_k: string[];
      movements: Record<string, Movement>;
      truffles:
        | { comparable: false; status: string | null; previous_status: string | null }
        | {
            comparable: boolean;
            count: number;
            previous_count: number;
            count_change: number;
            gained: string[];
            lost: string[];
          };
    };

export type SnapshotComparison =
  | { available: false; reason: string }
  | {
      available: true;
      previous_id: number;
      previous_frame_time: string;
      frame_gap_seconds: number;
      consecutive: boolean;
      same_frame: boolean;
      long: DirectionComparison;
      short: DirectionComparison;
    };

export interface RankingSnapshotDetail {
  snapshot: RankingSnapshotSummary;
  ranking: LiveRankings;
  previous: RankingSnapshotSummary | null;
  next: RankingSnapshotSummary | null;
  comparison: SnapshotComparison;
}

/** "↑ 3", "↓ 2", "–", or "new" — for a row present in the comparison. */
export function movementLabel(m: Movement | undefined, inTopK: boolean): string | null {
  if (!m) return null;
  if (m.previous_rank === null) return inTopK ? "new to top 10" : "new";
  if (m.change === 0) return "–";
  return m.change! > 0 ? `↑ ${m.change}` : `↓ ${-m.change!}`;
}

export function historyHref(snapshotId: number, extra: Record<string, string> = {}): string {
  const params = new URLSearchParams({ view: "history", snapshot: String(snapshotId), ...extra });
  return `/sniffer?${params.toString()}`;
}
