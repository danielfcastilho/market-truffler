import type {
  CampaignDetail,
  Condition,
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
  issues: [],
};

export const EXPERIMENT_DETAIL: ExperimentDetail = {
  experiment_id: BASE,
  spec: spec(BASE),
  campaigns: [{ campaign_id: CAMPAIGN, roles: ["baseline price-only-long", `reference in ${CMP}`] }],
  runs: [INSUFFICIENT_RUN],
  comparisons: [
    {
      campaign_id: CAMPAIGN,
      comparison: { comparison_id: CMP, hypothesis: "H1", candidate: CAND, reference: BASE },
      role: "reference",
      runs: [COMPARISON_RUN],
    },
  ],
  issues: [],
};

export const OVERVIEW: ResearchOverview = {
  counts: {
    campaigns: 1,
    hypotheses: 4,
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
  campaigns: [
    {
      campaign_id: CAMPAIGN,
      created: "2026-10-24",
      question: "Does Open Interest add information beyond price?",
      hypotheses: 4,
      experiments: 6,
      comparisons: 4,
      comparisons_executed: 4,
      comparisons_validated: 0,
      development_verdicts: { positive_evidence: 0, negative_evidence: 0, inconclusive: 0, insufficient_data: 4 },
      latest_activity: "2026-10-24T16:49:38+00:00",
    },
  ],
  recent_runs: [
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
