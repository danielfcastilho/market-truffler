import type {
  CampaignDetail,
  EvaluationSummary,
  Condition,
  LedgerRevision,
  ResearchHistory,
  ExperimentDetail,
  ExperimentSpec,
  ResearchOverview,
  RunRecord,
} from "@/lib/research";

/** Synthetic research artifacts for tests, shaped like real records. */

export const BASE = "base-long-v1";
export const CAND = "cand-long-v1";
export const CMP = "cand-vs-base-v1";
export const CAMPAIGN = "price-oi-v1";

const DOWN = { feature: "return_1h", transform: "cs_percentile", op: "<=", value: 0.2 } as const;
const OI_DOWN = { feature: "oi_change_1h", transform: "raw", op: "<", value: 0 } as const;

export function spec(id: string, conditions: Condition[] = [DOWN]): ExperimentSpec {
  return {
    spec_version: 1,
    experiment_id: id,
    heuristic: { id: "h", text: "Coins that fell may recover." },
    hypothesis: {
      statement: `Statement of ${id}.`,
      direction: "long",
      conditions: [...conditions],
    },
    datasets: [{ name: "shard_s15m_20261012", data_sha256: "4f74a041".padEnd(64, "0") }],
    label: "forward_relative_return_1h",
  };
}

export const INSUFFICIENT_RUN: RunRecord = {
  run_id: "20261024T164938019299Z-5f1287",
  experiment_id: BASE,
  executed_at: "2026-10-24T16:49:38+00:00",
  status: "completed",
  segment: "development",
  evaluator_version: 1,
  spec_hash: "a".repeat(64),
  hypothesis_hash: "b".repeat(64),
  datasets: [{ name: "shard_s15m_20261012", data_sha256: "4f74a041".padEnd(64, "0") }],
  report: {
    segment: "development",
    exploratory: true,
    days: ["2026-10-12"],
    counts: {
      timestamps_with_selection: 24,
      distinct_days: 1,
      distinct_selected_symbols: 689,
      selected_with_outcome: 3703,
    },
    outcomes: { selected_mean: -0.00159, baseline_mean: -0.00014 },
    spread: { mean: -0.00145 },
    uncertainty: {
      suppressed: true,
      reason: "fewer than 10 qualifying days — a day-block interval over so few days would be unreliable",
    },
    verdict: "insufficient_data",
    verdict_reasons: [
      "1 qualifying days in segment (< 10)",
      "24 timestamps with selections (< 200)",
    ],
    disclosures: ["No trading costs, slippage, funding payments, capacity, or execution are modeled."],
  },
};

export const COMPARISON_RUN: RunRecord = {
  run_id: "20261024T164938145671Z-73f37b",
  experiment_id: CMP,
  executed_at: "2026-10-24T16:49:38+00:00",
  kind: "incremental_comparison",
  status: "completed",
  segment: "development",
  comparison_version: 1,
  evaluator_version: 1,
  campaign_id: CAMPAIGN,
  campaign_hash: "c".repeat(64),
  candidate: { experiment_id: CAND },
  reference: { experiment_id: BASE },
  report: {
    segment: "development",
    counts: {
      timestamps_with_selection: 24,
      distinct_days_with_selection: 1,
      distinct_selected_symbols: 560,
      candidate_rows: 2138,
    },
    outcomes: { candidate_mean: -0.00176, reference_mean: -0.00159 },
    spread: { mean: 0.0001 },
    uncertainty: { suppressed: true, reason: "fewer than 10 qualifying days" },
    verdict: "insufficient_data",
    verdict_reasons: ["1 qualifying days in segment (< 10)"],
  },
};

export const POSITIVE_RUN: RunRecord = {
  ...INSUFFICIENT_RUN,
  run_id: "20261124T120000000000Z-abcdef",
  report: {
    ...INSUFFICIENT_RUN.report,
    verdict: "positive_evidence",
    verdict_reasons: [],
    spread: { mean: 0.0021 },
    uncertainty: {
      suppressed: false,
      method: "day-block bootstrap of the timestamp-weighted mean spread",
      confidence_level: 0.9,
      interval: [0.0004, 0.0038],
    },
  },
};

export const LEDGER: LedgerRevision = {
  ledger_schema_version: 1,
  knowledge_rules_version: 1,
  campaign_id: CAMPAIGN,
  campaign_hash: "c".repeat(64),
  scope: "feature_level",
  scope_note: "Feature-level knowledge only.",
  revision: 1,
  previous_revision_hash: null,
  derived_at: "2026-10-24T17:57:37+00:00",
  content_hash: "e".repeat(64),
  hypotheses: [
    {
      hypothesis_id: "H1",
      statement: "Declines with falling OI recover more.",
      prediction: "Positive incremental spread versus the price-only Long baseline.",
      prediction_sign: "positive",
      direction: "long",
      experiment_id: CAND,
      primary_evidence_ids: [CMP],
      knowledge_status: "insufficient_evidence",
      validation_status: "not_validated",
      evidence: [
        {
          experiment_id: CMP,
          run_id: COMPARISON_RUN.run_id,
          role: "primary",
          segment: "development",
          verdict: "insufficient_data",
          independent: null,
          protocol: "comparison-v1",
          spec_hash: "f".repeat(64),
          dataset_sha256: ["4f74a041".padEnd(64, "0")],
          executed_at: "2026-10-24T16:49:38+00:00",
          current: true,
        },
        {
          experiment_id: CMP,
          run_id: "20261024T100000000000Z-aaaaaa",
          role: "primary",
          segment: "development",
          verdict: "insufficient_data",
          independent: null,
          protocol: "comparison-v1",
          current: false,
        },
      ],
      limitations: [
        "development: 1 qualifying days in segment (< 10)",
        "No validation run: nothing is independently confirmed.",
      ],
    },
  ],
};

export const PENDING_EVALUATION: EvaluationSummary = {
  registration_id: "oi-price-1h-prospective-v1",
  registration_hash: "9".repeat(64),
  registered_at: "2026-10-24T20:13:10+00:00",
  campaign_id: CAMPAIGN,
  supersedes: null,
  protocol_version: 1,
  hypotheses: ["H1"],
  embargo_minutes: 240,
  timestamp_trust: "Local clock only.",
  partitions: [
    {
      role: "validation",
      start: "2026-10-26",
      end: "2026-11-25",
      state: "awaiting_data",
      eligible_from: "2026-11-27T00:00:00+00:00",
      days: 30,
      days_with_shard: 0,
      attempts: 0,
      exposed: false,
    },
    {
      role: "holdout",
      start: "2026-11-26",
      end: "2026-12-26",
      state: "awaiting_data",
      eligible_from: "2026-12-28T00:00:00+00:00",
      days: 30,
      days_with_shard: 0,
      attempts: 0,
      exposed: false,
    },
  ],
  events: [],
};

export const CAMPAIGN_DETAIL: CampaignDetail = {
  campaign: {
    campaign_id: CAMPAIGN,
    created: "2026-10-24",
    question: "Does Open Interest add information beyond price?",
    motivation: "Deleveraging may leave temporary dislocations.",
    hypotheses: [
      {
        id: "H1",
        statement: "Declines with falling OI recover more.",
        prediction: "Positive incremental spread versus the price-only Long baseline.",
        experiment_id: CAND,
      },
    ],
    baselines: [
      { id: "eligible-universe", description: "Built into the Evaluator." },
      { id: "price-only-long", description: "Bottom 20% of return_1h.", experiment_id: BASE },
    ],
    comparisons: [{ comparison_id: CMP, hypothesis: "H1", candidate: CAND, reference: BASE }],
    parameters_rationale: "Fixed before any outcome was examined.",
    evaluation_plan: "Development first; validation once.",
  },
  specs: { [CAND]: spec(CAND, [DOWN, OI_DOWN]), [BASE]: spec(BASE) },
  experiment_runs: { [CAND]: [], [BASE]: [INSUFFICIENT_RUN] },
  comparison_runs: { [CMP]: [COMPARISON_RUN] },
  campaign_hashes: ["c".repeat(64)],
  report: [
    { title: "", body: "# Report" },
    { title: "Conclusion", body: "**We learned nothing about the hypotheses yet.**\n\n- more days needed" },
    { title: "Knowledge status", body: "| H | Status |\n|---|---|\n| H1 | tested — insufficient data |" },
  ],
  knowledge_revisions: [LEDGER],
  knowledge_check: { problems: [], uncited_runs: [] },
  evaluations: [PENDING_EVALUATION],
  issues: [],
};

export const EXPERIMENT_DETAIL: ExperimentDetail = {
  experiment_id: CAND,
  spec: spec(CAND, [DOWN, OI_DOWN]),
  campaigns: [{ campaign_id: CAMPAIGN, roles: ["hypothesis H1", `candidate in ${CMP}`] }],
  knowledge: [
    {
      campaign_id: CAMPAIGN,
      revision: 1,
      hypothesis_id: "H1",
      knowledge_status: "insufficient_evidence",
      validation_status: "not_validated",
    },
  ],
  runs: [{ ...INSUFFICIENT_RUN, experiment_id: CAND }],
  comparisons: [
    {
      campaign_id: CAMPAIGN,
      comparison: { comparison_id: CMP, hypothesis: "H1", candidate: CAND, reference: BASE },
      role: "candidate",
      runs: [COMPARISON_RUN],
    },
  ],
  issues: [],
};

const SHORT_UP = { feature: "return_1h", transform: "cs_percentile", op: ">=", value: 0.8 } as const;

export const OVERVIEW: ResearchOverview = {
  counts: {
    campaigns: 1,
    hypotheses: 2,
    experiments_registered: 7,
    experiments_executed: 7,
    comparisons_registered: 4,
    comparisons_executed: 4,
    runs: 13,
    runs_failed: 0,
    latest_verdicts: {
      development: { positive_evidence: 0, negative_evidence: 0, inconclusive: 0, insufficient_data: 11 },
      validation: { positive_evidence: 0, negative_evidence: 0, inconclusive: 0, insufficient_data: 1 },
    },
  },
  knowledge: { by_status: { insufficient_evidence: 2 }, hypotheses_without_ledger: 0 },
  campaigns: [
    {
      campaign_id: CAMPAIGN,
      created: "2026-10-24",
      question: "Does Open Interest add information beyond price?",
      hypotheses: [
        {
          hypothesis_id: "H1",
          experiment_id: CAND,
          direction: "long",
          conditions: [DOWN, OI_DOWN],
          knowledge_status: "insufficient_evidence",
          validation_status: "not_validated",
        },
        {
          hypothesis_id: "H3",
          experiment_id: "short-v1",
          direction: "short",
          conditions: [SHORT_UP, OI_DOWN],
          knowledge_status: "insufficient_evidence",
          validation_status: "not_validated",
        },
      ],
      comparisons: 4,
      comparisons_executed: 4,
      ledger_revision: 1,
      ledger_derived_at: "2026-10-24T17:57:37+00:00",
      ledger_problems: 0,
      evaluations: [PENDING_EVALUATION],
      latest_activity: "2026-10-24T16:49:38+00:00",
    },
  ],
  activity: [
    {
      type: "knowledge_revision",
      at: "2026-10-24T17:57:37+00:00",
      campaign_id: CAMPAIGN,
      revision: 1,
      changes: [{ hypothesis_id: "H1", from: null, to: "insufficient_evidence" }],
    },
    {
      type: "evaluation",
      at: "2026-10-24T16:49:38+00:00",
      campaign_id: CAMPAIGN,
      experiment_ids: [CAND, CMP],
      runs: 10,
      repeat_runs: 0,
      failed_runs: 0,
      segments: ["development"],
      verdicts: { insufficient_data: 10 },
    },
    {
      type: "evaluation",
      at: "2026-10-12T20:16:46+00:00",
      campaign_id: null,
      experiment_ids: ["reversal-v1"],
      runs: 3,
      repeat_runs: 1,
      failed_runs: 0,
      segments: ["development", "validation"],
      verdicts: { insufficient_data: 3 },
    },
    { type: "campaign_registered", at: "2026-10-24", campaign_id: CAMPAIGN, title: "Does OI add information?" },
  ],
  issues: [],
};

export const HISTORY: ResearchHistory = {
  events: OVERVIEW.activity,
  runs: [
    {
      experiment_id: CMP,
      run_id: COMPARISON_RUN.run_id,
      kind: "incremental_comparison",
      campaign_id: CAMPAIGN,
      direction: "long",
      segment: "development",
      status: "completed",
      verdict: "insufficient_data",
      executed_at: "2026-10-24T16:49:38+00:00",
      link_experiment_id: CAND,
    },
  ],
  issues: [],
};
