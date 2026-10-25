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

export type KnowledgeStatus =
  | "untested"
  | "insufficient_evidence"
  | "inconclusive"
  | "preliminary_support"
  | "preliminary_contradiction"
  | "preliminary_difference"
  | "independently_supported"
  | "independently_contradicted"
  | "independently_observed_difference"
  | "conflicting_evidence";

export type ValidationStatus =
  | "not_validated"
  | "validation_not_independent"
  | "independently_validated"
  | "prospectively_validated"
  | "prospective_evidence_not_independent";

export type EvidenceClass =
  | "development"
  | "retrospective_validation"
  | "prospective_validation"
  | "protected_holdout"
  | "reused"
  | "independence_undetermined";

export type PartitionStateName =
  | "awaiting_data"
  | "eligible"
  | "authorized_only"
  | "exposure_uncertain"
  | "failed"
  | "failed_before_exposure"
  | "completed";

export interface PartitionState {
  role: "validation" | "holdout";
  start: string;
  end: string;
  state: PartitionStateName;
  eligible_from: string;
  days: number;
  days_with_shard: number;
  attempts: number;
  exposed: boolean;
}

export interface EvaluationSummary {
  registration_id: string;
  registration_hash: string;
  registered_at: string;
  campaign_id: string;
  supersedes: string | null;
  protocol_version: number;
  hypotheses: string[];
  embargo_minutes: number | null;
  timestamp_trust: string | null;
  partitions: PartitionState[];
  events?: { seq: number; at: string; type: string; role: string; attempt_id: string; independence?: string; error?: string }[];
}

export interface EvidenceReference {
  evidence_class?: EvidenceClass;
  registration_id?: string;
  independence?: string;
  experiment_id: string;
  run_id: string;
  role: "primary" | "context";
  segment: string;
  verdict: Verdict;
  independent?: boolean | null;
  protocol: string;
  spec_hash?: string;
  dataset_sha256?: string[];
  executed_at?: string;
  current: boolean;
}

export interface LedgerHypothesis {
  hypothesis_id: string;
  statement: string;
  prediction: string;
  prediction_sign: "positive" | "negative" | "none" | "unspecified";
  direction: Direction;
  experiment_id: string;
  primary_evidence_ids: string[];
  knowledge_status: KnowledgeStatus;
  validation_status: ValidationStatus;
  evidence: EvidenceReference[];
  limitations: string[];
}

export interface LedgerRevision {
  ledger_schema_version: number;
  knowledge_rules_version: number;
  campaign_id: string;
  campaign_hash: string;
  scope: string;
  scope_note?: string;
  revision: number;
  previous_revision_hash: string | null;
  derived_at: string;
  content_hash: string;
  hypotheses: LedgerHypothesis[];
}

export interface HypothesisSummary {
  hypothesis_id: string;
  experiment_id: string;
  direction: Direction | null;
  conditions: Condition[];
  knowledge_status: KnowledgeStatus | null;
  validation_status: ValidationStatus | null;
}

export type ResearchEvent =
  | { type: "campaign_registered"; at: string; campaign_id: string; title: string }
  | {
      type: "evaluation";
      at: string;
      campaign_id: string | null;
      experiment_ids: string[];
      runs: number;
      repeat_runs: number;
      failed_runs: number;
      segments: string[];
      verdicts: Partial<Record<Verdict, number>>;
    }
  | {
      type: "evaluation_registered";
      at: string;
      campaign_id: string;
      registration_id: string;
      hypotheses: string[];
      partitions: { role: string; start: string; end: string }[];
    }
  | {
      type: "ranking_evaluation";
      at: string;
      campaign_id: null;
      experiment_ids: string[];
      runs: number;
      repeat_runs: number;
      failed_runs: number;
      segments: string[];
      verdicts: Partial<Record<Verdict, number>>;
    }
  | {
      type: "knowledge_revision";
      at: string;
      campaign_id: string;
      revision: number;
      changes: { hypothesis_id: string; from: KnowledgeStatus | null; to: KnowledgeStatus }[];
    };

export interface RunSummary {
  experiment_id: string;
  run_id: string;
  kind: "experiment" | "incremental_comparison" | "prospective_evaluation" | "ranking_study";
  campaign_id: string | null;
  direction: Direction | null;
  segment: string | null;
  status: string;
  verdict: Verdict | null;
  executed_at: string | null;
  link_experiment_id: string | null;
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
  knowledge: {
    by_status: Partial<Record<KnowledgeStatus, number>>;
    hypotheses_without_ledger: number;
  };
  campaigns: {
    campaign_id: string;
    created: string | null;
    question: string;
    hypotheses: HypothesisSummary[];
    comparisons: number;
    comparisons_executed: number;
    ledger_revision: number | null;
    ledger_derived_at: string | null;
    ledger_problems: number;
    evaluations: EvaluationSummary[];
    latest_activity: string | null;
  }[];
  activity: ResearchEvent[];
  issues: ArtifactIssue[];
}

export interface ResearchHistory {
  events: ResearchEvent[];
  runs: RunSummary[];
  issues: ArtifactIssue[];
}

export interface CampaignDetail {
  campaign: Campaign;
  specs: Record<string, ExperimentSpec | null>;
  experiment_runs: Record<string, RunRecord[]>;
  comparison_runs: Record<string, RunRecord[]>;
  campaign_hashes: string[];
  report: { title: string; body: string }[] | null;
  knowledge_revisions: LedgerRevision[];
  knowledge_check: { problems: string[]; uncited_runs: string[] } | null;
  evaluations: EvaluationSummary[];
  issues: ArtifactIssue[];
}

export interface ExperimentDetail {
  experiment_id: string;
  spec: ExperimentSpec | null;
  campaigns: { campaign_id: string; roles: string[] }[];
  knowledge: {
    campaign_id: string;
    revision: number;
    hypothesis_id: string;
    knowledge_status: KnowledgeStatus;
    validation_status: ValidationStatus;
  }[];
  runs: RunRecord[];
  comparisons: {
    campaign_id: string;
    comparison: { comparison_id: string; hypothesis: string; candidate: string; reference: string };
    role: "candidate" | "reference";
    runs: RunRecord[];
  }[];
  issues: ArtifactIssue[];
}

// -- ranking research ---------------------------------------------------------------------

export interface RankingTerm {
  feature: string;
  transform: "identity" | "cs_percentile" | "cs_robust_z";
  weight: number;
}

export interface RankingCandidate {
  candidate_id: string;
  name: string;
  model_family: string;
  direction: Direction;
  objective: string;
  terms: RankingTerm[];
  missing_policy: string;
}

export interface RankingModelSummary {
  mean_top_k_excess_utility: number | null;
  timestamps: number;
  distinct_top_symbols: number;
  top_k_missing_outcome: number;
  diagnostics: {
    mean_top_k_utility: number | null;
    mean_top_bottom_separation: number | null;
    mean_spearman: number | null;
    daily_mean_excess_utility: Record<string, number>;
  };
}

export interface RankingRun {
  run_id: string;
  experiment_id: string;
  executed_at: string;
  status: string;
  error?: string | null;
  spec_hash: string;
  repeat_of: string[];
  ranking_evaluator_version: number;
  candidate: { candidate_id: string; spec_hash: string };
  reference: { candidate_id: string; spec_hash: string } | null;
  datasets?: { name: string; data_sha256: string }[];
  report?: {
    segment: string;
    primary_metric: string;
    top_k: number;
    horizon_minutes: number;
    days: string[];
    coverage: { timestamps: number; timestamps_universe_too_small: number; mean_universe: number | null; distinct_days: number };
    candidate: RankingModelSummary;
    reference?: RankingModelSummary;
    primary: {
      estimand: string;
      mean: number | null;
      matched_timestamps?: number;
      uncertainty: Uncertainty;
      verdict: Verdict;
      verdict_reasons: string[];
    };
    verdict: Verdict;
    verdict_reasons: string[];
    disclosures: string[];
  };
}

export interface RankingStudyView {
  study: {
    study_id: string;
    name: string;
    question: string;
    candidate: string;
    reference: string | null;
    datasets: { name: string; data_sha256: string }[];
    label: string;
    top_k: number;
  };
  candidate: RankingCandidate | null;
  reference: RankingCandidate | null;
  runs: RankingRun[];
}

export interface RankingResearch {
  candidates: RankingCandidate[];
  studies: RankingStudyView[];
  issues: ArtifactIssue[];
}

const TRANSFORM_WORDS: Record<RankingTerm["transform"], string> = {
  identity: "raw value",
  cs_percentile: "percentile rank within the frame",
  cs_robust_z: "robust z-score within the frame",
};

/** One scoring term in words, e.g. "−1 × percentile rank within the frame of 1-hour return". */
export function describeTerm(term: RankingTerm): string {
  const sign = term.weight < 0 ? "−" : "+";
  return `${sign}${Math.abs(term.weight)} × ${TRANSFORM_WORDS[term.transform]} of ${describeFeature(term.feature).toLowerCase()}`;
}

// -- knowledge --------------------------------------------------------------------------------

/**
 * Names and meanings of the knowledge ledger's statuses (knowledge rules
 * v1, `app.research.knowledge`). The ledger decides them; this only names
 * them. "Established" is deliberately absent: no status here means a
 * finding is settled, and only the independently_* ones involve validation.
 */
export const KNOWLEDGE_META: Record<
  KnowledgeStatus,
  { label: string; meaning: string; tone: "muted" | "neutral" | "preliminary" | "supported" | "against" }
> = {
  untested: {
    label: "Untested",
    meaning: "Registered, but no evaluation has completed yet.",
    tone: "muted",
  },
  insufficient_evidence: {
    label: "Insufficient evidence",
    meaning:
      "Evaluated, but the evidence did not meet the minimum requirements. Nothing is established in either direction.",
    tone: "muted",
  },
  inconclusive: {
    label: "Inconclusive",
    meaning: "Enough data was evaluated, but no directional conclusion was justified.",
    tone: "neutral",
  },
  preliminary_support: {
    label: "Preliminary support",
    meaning:
      "Development evidence agrees with the registered prediction. Exploratory only — not independently validated.",
    tone: "preliminary",
  },
  preliminary_contradiction: {
    label: "Preliminary contradiction",
    meaning:
      "Development evidence disagrees with the registered prediction. Exploratory only — not independently validated.",
    tone: "preliminary",
  },
  preliminary_difference: {
    label: "Preliminary difference",
    meaning:
      "Outcomes differed in development, but the prediction registered no direction, so nothing is supported. Not validated.",
    tone: "preliminary",
  },
  independently_supported: {
    label: "Independently supported",
    meaning:
      "Development and an independent validation both agree with the registered prediction — a feature-level result in these samples, not proof a ranking model improves.",
    tone: "supported",
  },
  independently_contradicted: {
    label: "Independently contradicted",
    meaning:
      "Development and an independent validation both disagree with the registered prediction, in these samples.",
    tone: "against",
  },
  independently_observed_difference: {
    label: "Independently observed difference",
    meaning:
      "Development and an independent validation show the same directional difference; the prediction registered no direction.",
    tone: "supported",
  },
  conflicting_evidence: {
    label: "Conflicting evidence",
    meaning: "Development and independent validation point in opposite directions.",
    tone: "against",
  },
};

export const VALIDATION_META: Record<ValidationStatus, string> = {
  not_validated: "Not validated — no validation run exists",
  validation_not_independent: "Validation run reused earlier validation days — not independent",
  independently_validated: "Independent (retrospective) validation run recorded",
  prospectively_validated: "Evaluated prospectively on data unseen when the claim was frozen",
  prospective_evidence_not_independent:
    "A prospective evaluation ran, but its independence could not be established",
};

export const EVIDENCE_CLASS_LABEL: Record<EvidenceClass, string> = {
  development: "Development (exploratory)",
  retrospective_validation: "Retrospective validation",
  prospective_validation: "Prospective validation",
  protected_holdout: "Protected holdout",
  reused: "Reused data — not independent",
  independence_undetermined: "Independence undetermined",
};

export const PARTITION_STATE_LABEL: Record<PartitionStateName, string> = {
  awaiting_data: "Awaiting data",
  eligible: "Eligible — not yet evaluated",
  authorized_only: "Authorized, never started",
  exposure_uncertain: "Interrupted — exposure uncertain",
  failed: "Failed after possible exposure",
  failed_before_exposure: "Failed before any data was read",
  completed: "Completed",
};

/**
 * "Has this hypothesis been independently tested on genuinely unseen
 * data?" — yes only when the knowledge ledger cites independent
 * prospective evidence (never because a plan is registered).
 */
export function unseenDataAnswer(
  hypothesisId: string,
  entry: LedgerHypothesis | null | undefined,
  evaluations: EvaluationSummary[],
): { tested: boolean; text: string } {
  const independent = entry?.evidence.filter(
    (e) =>
      e.current &&
      (e.evidence_class === "prospective_validation" || e.evidence_class === "protected_holdout"),
  );
  if (independent && independent.length > 0) {
    const roles = [...new Set(independent.map((e) => (e.evidence_class === "protected_holdout" ? "protected holdout" : "prospective validation")))];
    return { tested: true, text: `Yes — ${roles.join(" and ")} on data unseen when the claim was frozen.` };
  }
  const plans = evaluations.filter((e) => e.hypotheses.includes(hypothesisId));
  if (plans.length === 0) {
    return { tested: false, text: "No — no prospective evaluation is registered; all evidence is historical." };
  }
  const validation = plans.flatMap((e) => e.partitions).find((p) => p.role === "validation");
  if (!validation) return { tested: false, text: "Not yet — a prospective evaluation is registered." };
  if (validation.state === "awaiting_data") {
    return {
      tested: false,
      text: `Not yet — prospective validation registered for ${validation.start} to ${validation.end}; awaiting data (eligible from ${validation.eligible_from.slice(0, 10)}).`,
    };
  }
  if (validation.state === "eligible") {
    return { tested: false, text: "Not yet — prospective validation is eligible but has not been run." };
  }
  if (validation.state === "completed") {
    return {
      tested: false,
      text: "No — the prospective validation ran, but the ledger does not count it as independent evidence.",
    };
  }
  return { tested: false, text: `No — the prospective validation ${PARTITION_STATE_LABEL[validation.state].toLowerCase()}.` };
}

export function isKnowledgeStatus(value: unknown): value is KnowledgeStatus {
  return typeof value === "string" && value in KNOWLEDGE_META;
}

const FAMILY: [RegExp, string][] = [
  [/^return_/, "Price"],
  [/^oi_change_/, "Open Interest"],
  [/^rsi_14_/, "RSI"],
  [/^volatility_/, "Volatility"],
  [/^relative_volume_/, "Relative volume"],
  [/^funding_rate_/, "Funding"],
];

/** A condition as a compact symbol: low (↓) or high (↑), or the raw rule. */
export function conditionShort(condition: Condition): string {
  const family = FAMILY.find(([pattern]) => pattern.test(condition.feature))?.[1] ?? condition.feature;
  const low = condition.op === "<" || condition.op === "<=";
  if (condition.transform === "cs_percentile") return `${family} ${low ? "↓" : "↑"}`;
  // An arrow only stands for a sign test (against exactly 0); any other
  // threshold is shown as written.
  if (condition.value === 0) return `${family} ${low ? "↓" : "↑"}`;
  return `${family} ${condition.op} ${condition.value}`;
}

/**
 * A hypothesis' readable title from its registered conditions, e.g.
 * "Price ↓ + Open Interest ↓ · 1h". The exact conditions are always shown
 * next to it — the title is a label, not the definition.
 */
export function hypothesisTitle(conditions: Condition[]): string {
  if (conditions.length === 0) return "No conditions";
  const timeframes = new Set(conditions.map((c) => c.feature.match(/_(\d+[mhd])$/)?.[1] ?? "?"));
  const suffix = timeframes.size === 1 && !timeframes.has("?") ? ` · ${[...timeframes][0]}` : "";
  return conditions.map(conditionShort).join(" + ") + suffix;
}

export function predictionKind(sign: LedgerHypothesis["prediction_sign"]): string {
  if (sign === "positive" || sign === "negative") return "Directional prediction";
  return "No direction registered — only a difference can be observed";
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


// -- model knowledge (`GET /api/research/models`) ---------------------------------------------

export type ModelStatus =
  | "experimental"
  | "registered_for_prospective_evaluation"
  | "insufficient_prospective_evidence"
  | "inconclusive_prospective_evidence"
  | "supported_by_prospective_evidence"
  | "contradicted_by_prospective_evidence"
  | "conflicting_prospective_evidence";

export interface ModelEvidence {
  class: string;
  ref: string;
  verdict?: string | null;
  decision?: string | null;
  hypothesis_id?: string;
  via_qualification_policy?: string;
  temporal_contract_versions?: number[];
  text?: string;
}

export interface ModelKnowledgeEntry {
  kind: "ranking_model" | "qualification_policy";
  id: string;
  revision: number;
  revisions: number;
  derived_at: string;
  content_hash: string;
  status: ModelStatus;
  status_basis: string[];
  identity: {
    kind: string;
    id: string;
    spec_hash: string;
    spec_version: number;
    direction: "long" | "short";
    features: string[];
    model_family?: string;
    applies_to?: { ranking_model: string; direction: string };
    parameters: Record<string, unknown>;
  };
  evidence_counts: Record<string, number>;
  evidence: ModelEvidence[];
  links: Record<string, unknown>;
  limitations: string[];
  chain_problems: string[];
}

export interface TruffleRegistrationView {
  registration_id: string;
  registration_hash: string;
  registered_at: string;
  question: string;
  claims: {
    hypothesis_id: string;
    direction: "long" | "short";
    statement: string;
    ranking_candidate: string;
    ranking_spec_hash: string;
    qualification_id: string;
    qualification_spec_hash: string;
    primary_label: string;
    secondary_labels: string[];
  }[];
  plan: { primary_reference: string; multiplicity: { method: string; family_size: number; adjusted_confidence: number } } & Record<string, unknown>;
  limitations: string[];
  partitions: {
    role: string;
    start: string;
    end: string;
    lifecycle: string;
    eligible_from: string;
    days: number;
    days_with_shard: number;
    decisions: Record<string, string>;
  }[];
}

export interface ModelKnowledge {
  models: ModelKnowledgeEntry[];
  truffle_registrations: TruffleRegistrationView[];
  issues: { artifact: string; problem: string }[];
}

const MODEL_STATUS_LABEL: Record<ModelStatus, string> = {
  experimental: "Experimental",
  registered_for_prospective_evaluation: "Registered — awaiting prospective evidence",
  insufficient_prospective_evidence: "Insufficient prospective evidence",
  inconclusive_prospective_evidence: "Inconclusive prospective evidence",
  supported_by_prospective_evidence: "Supported by prospective evidence",
  contradicted_by_prospective_evidence: "Contradicted by prospective evidence",
  conflicting_prospective_evidence: "Conflicting prospective evidence",
};

export function modelStatusLabel(status: string): string {
  return MODEL_STATUS_LABEL[status as ModelStatus] ?? status;
}

const LIFECYCLE_LABEL: Record<string, string> = {
  registered_awaiting_data: "Registered — awaiting data",
  eligible_for_evaluation: "Eligible for evaluation",
  evaluated: "Evaluated",
  exposure_uncertain: "Exposure uncertain",
  failed: "Failed",
};

export function lifecycleLabel(lifecycle: string): string {
  return LIFECYCLE_LABEL[lifecycle] ?? lifecycle;
}


// -- research data health (`GET /api/research/data-health`) --------------------------------

export interface DailyCoverage {
  day: string;
  partial_day: "first" | "last" | "first_and_last" | null;
  expected: number;
  finalized: number;
  complete: number;
  partial: number;
  building: number;
  missing: number;
  analyzed: number;
  unanalyzed: number;
  analysis_unknown: number;
  coverage: number;
  largest_gap_minutes: number;
  analyzed_by_contract: { v1: number; v2: number };
}

export interface Intervals {
  count: number;
  truncated: boolean;
  items: [string, string, number][];
}

export type FrameCoverage =
  | { available: false; reason: string; off_grid_frames: number }
  | {
      available: true;
      observed_from: string;
      observed_to: string;
      observed_note: string;
      expected: number;
      finalized: number;
      missing: number;
      building: number;
      partial: number;
      complete: number;
      analyzed: number;
      unanalyzed: number;
      analysis_unknown: number;
      coverage: number;
      latest_frame: string;
      off_grid_frames: number;
      duplicate_rows: number;
      results_retained_from: string | null;
      covered_intervals: Intervals;
      missing_intervals: Intervals;
      analyzed_by_contract: Record<string, { frames: number; first: string | null; last: string | null }>;
      daily: DailyCoverage[];
    };

export interface ShardEntry {
  name: string;
  kind: "daily_shard" | "dataset";
  manifest: string;
  data_file_present: boolean;
  manifest_sha256?: string;
  data_sha256?: string | null;
  shard_day?: string | null;
  first_frame_time?: string | null;
  last_frame_time?: string | null;
  row_count?: number | null;
  generated_at?: string | null;
  /** "reconstructed" (exploratory only, never pooled with live data) or "live_frames". */
  observation_source?: string | null;
  temporal_contract_version?: number | null;
  manifest_problems?: string[];
  verification: VerificationStatus;
  verification_scope?: string | null;
  verified_at?: string | null;
  verification_overdue?: boolean;
  verification_error?: string;
}

export type VerificationStatus =
  | "never_checked"
  | "pending"
  | "verified"
  | "restricted"
  | "failed"
  | "unavailable";

export interface RecentWindow {
  from: string;
  to: string;
  window_shortened: boolean;
  expected: number;
  finalized: number;
  missing: number;
  never_finalized: number;
  coverage: number | null;
  analysis: { finalized_with_members: number; analyzed: number; pending: number; completion: number | null };
}

export interface ReadinessPartition {
  role: string;
  start: string;
  end: string;
  state: string;
  phase: string;
  eligible_from: string;
  days: number;
  days_with_shard: number;
  days_elapsed: number;
  missing_past_days: string[];
  blocking: string[];
  eligible_now: boolean;
}

export interface RegistrationReadiness {
  registration_id: string | null;
  kind?: string;
  registration_hash?: string;
  registered_at?: string;
  verification: "verified" | "invalid" | "error";
  problems: string[];
  partitions: ReadinessPartition[];
}

export interface DataHealth {
  computed_at: string;
  cache_seconds: number;
  frames: FrameCoverage;
  recent_continuity?: { available: false; reason: string } | { available: true; windows: Record<string, RecentWindow> };
  verification?:
    | { available: false; reason: string }
    | {
        available: true;
        interval_hours: number;
        last_checked_at: string | null;
        last_pass_at: string | null;
        last_pass_checked: number;
        counts: Partial<Record<VerificationStatus, number>>;
        scopes: Record<string, string>;
      };
  alerts?: ResearchAlert[];
  backups?:
    | { available: false; reason: string }
    | { available: true; configured: false; reason: string }
    | {
        available: true;
        configured: true;
        destination_kind: string | null;
        last_attempt_at: string | null;
        last_success_at: string | null;
        destination_error: string | null;
        items_in_scope: number | null;
        protected: number;
        unprotected: number;
        bytes: number | null;
        failed_items: number;
        integrity_failures: number;
        recovery_test: {
          status: string;
          tested_at: string;
          scope: string;
          items_tested: number;
          restricted_items: number;
          failures: number;
          error: string | null;
        } | null;
      };
  shards: { datasets: ShardEntry[]; overlaps: { datasets: string[]; kind: string }[] };
  shard_continuity:
    | { available: false; reason: string }
    | {
        available: true;
        reported_at: string | null;
        shards_start: string | null;
        days: number;
        by_status: Record<string, { count: number; days: string[] }>;
        status_meanings: Record<string, string>;
      };
  temporal_contract: {
    current_version: number;
    frames: Record<string, { frames: number; first: string | null; last: string | null }> | null;
    datasets: Record<string, { count: number; names: string[] }>;
    meaning: Record<string, string>;
  };
  readiness:
    | { available: false; reason: string; registrations: [] }
    | { available: true; reported_at: string | null; registrations: RegistrationReadiness[] };
  warnings: string[];
  limitations: string[];
}

const PHASE_LABEL: Record<string, string> = {
  awaiting_window: "Awaiting its window",
  accumulating: "Accumulating data",
  awaiting_eligibility: "Window ended — awaiting eligibility",
  eligible: "Eligible for evaluation",
  evaluated: "Evaluated",
  failed: "Attempt failed",
  exposure_uncertain: "Exposure uncertain",
  authorized_only: "Authorized only",
};

export function phaseLabel(phase: string): string {
  return PHASE_LABEL[phase] ?? phase;
}

export function formatPercent(fraction: number): string {
  return `${(fraction * 100).toFixed(1)}%`;
}

/** A prospective continuity alert (`app.research.research_alerts`). */
export interface ResearchAlert {
  id: string;
  registration_id: string | null;
  role: string | null;
  condition: string;
  severity: "critical" | "warning" | "info";
  explanation: string;
  days: string[];
  interval: [string, string] | null;
  first_observed: string | null;
  last_checked: string | null;
  state: string;
  occurrence?: number;
}
