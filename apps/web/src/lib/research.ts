/**
 * OINK CORP Research Explorer: types for the read-only research API and
 * pure presentation helpers. Nothing here evaluates evidence — verdicts are
 * exactly what the backend Evaluator (or comparison protocol) recorded;
 * these helpers only name them and translate technical definitions into
 * words without changing their meaning.
 */

export type Verdict = "positive_evidence" | "negative_evidence" | "inconclusive" | "insufficient_data";
export type Direction = "long" | "short";

export interface Condition {
  feature: string;
  transform: "raw" | "cs_percentile";
  op: "<" | "<=" | ">" | ">=";
  value: number;
}

export interface ExperimentSpec {
  spec_version: number;
  experiment_id: string;
  heuristic: { id: string; text: string };
  hypothesis: { statement: string; direction: Direction; conditions: Condition[] };
  datasets: { name: string; data_sha256: string }[];
  label: string;
}

export interface Uncertainty {
  suppressed: boolean;
  reason?: string;
  method?: string;
  confidence_level?: number;
  interval?: [number, number];
}

/** Report fields shared by Evaluator v1 and comparison protocol v1 runs. */
export interface RunReport {
  segment?: string;
  exploratory?: boolean;
  comparison_version?: number;
  days?: string[];
  excluded_days?: string[];
  counts?: Record<string, number>;
  outcomes?: Record<string, number | string | null>;
  spread?: { mean?: number | null; positive_day_fraction?: number | null } & Record<string, unknown>;
  uncertainty?: Uncertainty;
  validation_reuse?: { independent?: boolean; prior_validation_runs_overlapping?: number };
  verdict?: Verdict;
  verdict_reasons?: string[];
  disclosures?: string[];
}

export interface RunRecord {
  run_id: string;
  experiment_id: string;
  executed_at?: string;
  kind?: string;
  status?: string;
  error?: string | null;
  segment?: string;
  spec_hash?: string;
  hypothesis_hash?: string;
  campaign_id?: string;
  campaign_hash?: string;
  evaluator_version?: number;
  comparison_version?: number;
  label?: string;
  git_commit?: string | null;
  code_fingerprint?: string;
  seed?: number;
  datasets?: { name: string; data_sha256: string }[];
  candidate?: { experiment_id: string; spec_hash?: string };
  reference?: { experiment_id: string; spec_hash?: string };
  report?: RunReport;
}

export interface CampaignHypothesis {
  id: string;
  statement: string;
  prediction: string;
  experiment_id: string;
}

export interface Campaign {
  campaign_id: string;
  created: string;
  question: string;
  motivation: string;
  hypotheses: CampaignHypothesis[];
  baselines: { id: string; description: string; experiment_id?: string | null }[];
  comparisons: { comparison_id: string; hypothesis: string; candidate: string; reference: string }[];
  parameters_rationale: string;
  evaluation_plan: string;
}

export interface ArtifactIssue {
  artifact: string;
  problem: string;
}

export interface ResearchOverview {
  counts: {
    campaigns: number;
    hypotheses: number;
    experiments_registered: number;
    experiments_executed: number;
    comparisons_registered: number;
    comparisons_executed: number;
    runs: number;
    runs_failed: number;
    latest_verdicts: Record<string, Record<Verdict, number>>;
  };
  campaigns: {
    campaign_id: string;
    created: string | null;
    question: string;
    hypotheses: number;
    experiments: number;
    comparisons: number;
    comparisons_executed: number;
    comparisons_validated: number;
    development_verdicts: Record<Verdict, number>;
    latest_activity: string | null;
  }[];
  recent_runs: {
    experiment_id: string;
    run_id: string;
    kind: "experiment" | "incremental_comparison";
    campaign_id: string | null;
    direction: Direction | null;
    segment: string | null;
    status: string;
    verdict: Verdict | null;
    executed_at: string | null;
    link_experiment_id: string | null;
  }[];
  issues: ArtifactIssue[];
}

export interface CampaignDetail {
  campaign: Campaign;
  specs: Record<string, ExperimentSpec | null>;
  experiment_runs: Record<string, RunRecord[]>;
  comparison_runs: Record<string, RunRecord[]>;
  campaign_hashes: string[];
  report: { title: string; body: string }[] | null;
  issues: ArtifactIssue[];
}

export interface ExperimentDetail {
  experiment_id: string;
  spec: ExperimentSpec | null;
  campaigns: { campaign_id: string; roles: string[] }[];
  runs: RunRecord[];
  comparisons: {
    campaign_id: string;
    comparison: { comparison_id: string; hypothesis: string; candidate: string; reference: string };
    role: "candidate" | "reference";
    runs: RunRecord[];
  }[];
  issues: ArtifactIssue[];
}

// -- verdicts -----------------------------------------------------------------------------

export const VERDICTS: Verdict[] = [
  "positive_evidence",
  "negative_evidence",
  "inconclusive",
  "insufficient_data",
];

/** Names and meanings of the backend's own verdicts (Evaluator v1 rules). */
export const VERDICT_META: Record<Verdict, { label: string; meaning: string }> = {
  positive_evidence: {
    label: "Positive evidence",
    meaning:
      "The 90% day-block interval lies above zero in the hypothesis' direction, at least 60% of days agree, and no single coin dominates — in this segment only.",
  },
  negative_evidence: {
    label: "Negative evidence",
    meaning:
      "The 90% day-block interval lies below zero — against the hypothesis' direction — and at least 60% of days agree, in this segment only.",
  },
  inconclusive: {
    label: "Inconclusive",
    meaning:
      "Enough data was evaluated, but the interval, day-to-day consistency or concentration criteria were not met: no directional conclusion.",
  },
  insufficient_data: {
    label: "Insufficient data",
    meaning:
      "The minimum-evidence requirements were not met. No conclusion is justified — neither support nor contradiction.",
  },
};

export function isVerdict(value: unknown): value is Verdict {
  return typeof value === "string" && (VERDICTS as string[]).includes(value);
}

export function segmentLabel(segment: string | undefined | null): string {
  if (segment === "development") return "Development (exploratory)";
  if (segment === "validation") return "Validation";
  return segment ?? "Unknown segment";
}

// -- definitions in words --------------------------------------------------------------------

const TIMEFRAME_WORDS: Record<string, string> = {
  "5m": "5-minute",
  "15m": "15-minute",
  "1h": "1-hour",
  "4h": "4-hour",
  "24h": "24-hour",
  "1d": "1-day",
};

function timeframe(tf: string): string {
  return TIMEFRAME_WORDS[tf] ?? tf;
}

/** A Sniffer feature name in words; unknown names are returned unchanged. */
export function describeFeature(feature: string): string {
  const fixed: Record<string, string> = {
    funding_rate_current: "Current funding rate",
    funding_rate_24h_avg: "24-hour average funding rate",
  };
  if (fixed[feature]) return fixed[feature];
  const patterns: [RegExp, (tf: string) => string][] = [
    [/^return_(\w+)$/, (tf) => `${timeframe(tf)} return`],
    [/^oi_change_(\w+)$/, (tf) => `${timeframe(tf)} Open Interest change`],
    [/^rsi_14_(\w+)$/, (tf) => `${timeframe(tf)} RSI(14)`],
    [/^volatility_(\w+)$/, (tf) => `${timeframe(tf)} volatility`],
    [/^relative_volume_(\w+)$/, (tf) => `${timeframe(tf)} relative volume`],
  ];
  for (const [pattern, words] of patterns) {
    const match = feature.match(pattern);
    if (match) {
      const text = words(match[1]);
      return text.charAt(0).toUpperCase() + text.slice(1);
    }
  }
  return feature;
}

function percent(value: number): string {
  return `${Number((value * 100).toFixed(2))}%`;
}

/**
 * One condition in words. `cs_percentile` is the fraction of instruments
 * in the same frame (with that feature available) whose value is at or
 * below this one — so `<= 0.2` is "the bottom 20%".
 */
export function describeCondition(condition: Condition): string {
  const feature = describeFeature(condition.feature);
  const { op, value } = condition;
  if (condition.transform === "cs_percentile") {
    const scope = "of instruments in the same frame";
    if (op === "<=") return `${feature} is in the bottom ${percent(value)} ${scope}`;
    if (op === ">=") return `${feature} is in the top ${percent(1 - value)} ${scope}`;
    if (op === "<") return `${feature} ranks below the ${percent(value)} mark ${scope}`;
    return `${feature} ranks above the ${percent(value)} mark ${scope}`;
  }
  if (value === 0 && (op === "<" || op === ">")) {
    return `${feature} is ${op === "<" ? "negative (below 0)" : "positive (above 0)"}`;
  }
  const words = { "<": "is below", "<=": "is at most", ">": "is above", ">=": "is at least" }[op];
  return `${feature} ${words} ${value}`;
}

export function technicalCondition(condition: Condition): string {
  const left =
    condition.transform === "cs_percentile"
      ? `cs_percentile(${condition.feature})`
      : condition.feature;
  return `${left} ${condition.op} ${condition.value}`;
}

/** An outcome label in words, e.g. forward_relative_return_1h. */
export function describeLabel(label: string): string {
  const match = label.match(/^forward_(relative_)?return_(\w+)$/);
  if (!match) return label;
  const horizon = timeframe(match[2]);
  return match[1]
    ? `Next ${horizon} return relative to the market (minus that frame's median return)`
    : `Next ${horizon} return`;
}

export function directionNote(direction: Direction): string {
  return direction === "short"
    ? "Short: outcomes are sign-flipped, so a coin falling relative to the market counts as a positive outcome."
    : "Long: a coin rising relative to the market counts as a positive outcome.";
}

// -- numbers ----------------------------------------------------------------------------------

/** A return (fraction) as a signed percentage: -0.00159 -> "−0.159%". */
export function formatReturn(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const text = Math.abs(value * 100).toFixed(3);
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${text}%`;
}

/** A difference between two returns, in percentage points. */
export function formatSpread(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const text = Math.abs(value * 100).toFixed(3);
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${text} pp`;
}

export function formatCount(value: number | null | undefined): string {
  return value == null ? "—" : value.toLocaleString("en-US");
}

export function shortHash(hash: string | null | undefined): string {
  return hash ? `${hash.slice(0, 12)}…` : "—";
}

export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return `${date.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

// -- runs ------------------------------------------------------------------------------------

export function verdictOf(record: RunRecord): Verdict | null {
  if (record.status !== "completed") return null;
  return isVerdict(record.report?.verdict) ? record.report!.verdict! : null;
}

/** The newest run per segment (records arrive newest first). */
export function latestBySegment(records: RunRecord[]): Record<string, RunRecord> {
  const latest: Record<string, RunRecord> = {};
  for (const record of records) {
    const segment = record.segment ?? "unknown";
    if (!(segment in latest)) latest[segment] = record;
  }
  return latest;
}

export interface EvidenceFigures {
  kind: "experiment" | "comparison";
  timestampsWithSelection: number | null;
  days: number | null;
  daysLabel: string;
  symbols: number | null;
  selectedRows: number | null;
  selectedMean: number | null;
  baselineMean: number | null;
  baselineLabel: string;
  spread: number | null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** The recorded figures of a run, named for what they are — no derivation. */
export function evidenceFigures(record: RunRecord): EvidenceFigures {
  const report = record.report ?? {};
  const counts = report.counts ?? {};
  const outcomes = report.outcomes ?? {};
  if (record.kind === "incremental_comparison") {
    return {
      kind: "comparison",
      timestampsWithSelection: num(counts.timestamps_with_selection),
      days: num(counts.distinct_days_with_selection),
      daysLabel: "Days with selections",
      symbols: num(counts.distinct_selected_symbols),
      selectedRows: num(counts.candidate_rows),
      selectedMean: num(outcomes.candidate_mean),
      baselineMean: num(outcomes.reference_mean),
      baselineLabel: "Reference group mean",
      spread: num(report.spread?.mean),
    };
  }
  return {
    kind: "experiment",
    timestampsWithSelection: num(counts.timestamps_with_selection),
    days: num(counts.distinct_days),
    daysLabel: "Days in segment",
    symbols: num(counts.distinct_selected_symbols),
    selectedRows: num(counts.selected_with_outcome),
    selectedMean: num(outcomes.selected_mean),
    baselineMean: num(outcomes.baseline_mean),
    baselineLabel: "Eligible universe mean",
    spread: num(report.spread?.mean),
  };
}
