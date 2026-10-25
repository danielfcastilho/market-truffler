import type {
  CampaignDetail,
  EvaluationSummary,
  Condition,
  LedgerRevision,
  ResearchHistory,
  ExperimentDetail,
  ExperimentSpec,
  ModelKnowledge,
  ModelKnowledgeEntry,
  RankingCandidate,
  RankingResearch,
  RankingRun,
  ResearchOverview,
  RunRecord,
  TruffleRegistrationView,
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

// -- ranking studies, model knowledge, live models ------------------------------------------

const PRICE_TERM = { feature: "return_1h", transform: "cs_percentile", weight: -1 } as const;
const OI_TERM = { feature: "oi_change_1h", transform: "cs_percentile", weight: -0.5 } as const;
export const LIVE_HASH = "b".repeat(64);

export function rankingModel(
  id: string,
  name: string,
  direction: "long" | "short",
  terms: RankingCandidate["terms"],
): RankingCandidate {
  return { candidate_id: id, name, model_family: "linear_weighted_v1", direction, objective: "o", terms, missing_policy: "exclude" };
}

const STUDY_SUMMARY = (q: number) => ({
  mean_top_k_excess_utility: q,
  timestamps: 24,
  distinct_top_symbols: 182,
  top_k_missing_outcome: 15,
  diagnostics: { mean_top_k_utility: -0.0022, mean_top_bottom_separation: -0.0038, mean_spearman: -0.1074, daily_mean_excess_utility: {} },
});

export const STUDY_RUN: RankingRun = {
  run_id: "20261024T203608443251Z-fc261a",
  experiment_id: "long-oi-vs-price-v1",
  executed_at: "2026-10-24T20:36:08+00:00",
  status: "completed",
  spec_hash: "a".repeat(64),
  repeat_of: [],
  ranking_evaluator_version: 1,
  candidate: { candidate_id: "long-oi-v1", spec_hash: LIVE_HASH },
  reference: { candidate_id: "long-price-v1", spec_hash: "c".repeat(64) },
  datasets: [{ name: "shard_s15m_20261012", data_sha256: "d".repeat(64) }],
  report: {
    segment: "development",
    primary_metric: "top_k_excess_utility",
    top_k: 20,
    horizon_minutes: 60,
    days: ["2026-10-12"],
    coverage: { timestamps: 24, timestamps_universe_too_small: 0, mean_universe: 795, distinct_days: 1 },
    candidate: STUDY_SUMMARY(-0.00206),
    reference: STUDY_SUMMARY(-0.00112),
    primary: {
      estimand: "paired",
      mean: -0.00094,
      matched_timestamps: 24,
      uncertainty: { suppressed: true, reason: "fewer than 10 qualifying days" },
      verdict: "insufficient_data",
      verdict_reasons: ["1 qualifying days in segment (< 10)", "24 evaluated timestamps (< 200)"],
    },
    verdict: "insufficient_data",
    verdict_reasons: ["1 qualifying days in segment (< 10)", "24 evaluated timestamps (< 200)"],
    disclosures: ["Weights are manually specified research parameters; nothing was optimized."],
  },
};

export const RANKING_RESEARCH: RankingResearch = {
  candidates: [],
  studies: [
    {
      study: {
        study_id: "long-oi-vs-price-v1",
        name: "Long: does Open Interest improve a price-reversal ranking?",
        question: "Do the top 20 of the price + OI ranking beat the price-only top 20?",
        candidate: "long-oi-v1",
        reference: "long-price-v1",
        datasets: [],
        label: "forward_relative_return_1h",
        top_k: 20,
      },
      candidate: rankingModel("long-oi-v1", "Long — price reversal + falling Open Interest", "long", [PRICE_TERM, OI_TERM]),
      reference: rankingModel("long-price-v1", "Long — price reversal (price only)", "long", [PRICE_TERM]),
      runs: [STUDY_RUN],
    },
    {
      study: {
        study_id: "short-oi-vs-price-v1",
        name: "Short: does Open Interest improve a price-reversal ranking?",
        question: "q",
        candidate: "short-oi-v1",
        reference: null,
        datasets: [],
        label: "forward_relative_return_1h",
        top_k: 20,
      },
      candidate: rankingModel("short-oi-v1", "Short — price reversal", "short", [{ ...PRICE_TERM, weight: 1 }]),
      reference: null,
      runs: [],
    },
  ],
  issues: [],
};

export function ledgerEntry(
  id: string,
  kind: ModelKnowledgeEntry["kind"],
  status: string,
  extra: Partial<ModelKnowledgeEntry> = {},
): ModelKnowledgeEntry {
  return {
    kind,
    id,
    revision: 2,
    revisions: 2,
    derived_at: "2026-10-25T01:50:00+00:00",
    content_hash: "e".repeat(64),
    status: status as ModelKnowledgeEntry["status"],
    status_basis: [],
    identity: {
      kind,
      id,
      spec_hash: LIVE_HASH,
      spec_version: 1,
      direction: "long",
      features: ["return_1h", "oi_change_1h"],
      model_family: kind === "ranking_model" ? "linear_weighted_v1" : undefined,
      applies_to: kind === "qualification_policy" ? { ranking_model: "long-oi-v1", direction: "long" } : undefined,
      parameters:
        kind === "qualification_policy"
          ? {
              conditions: [
                { feature: "return_1h", measure: "percentile", at_most: 0.05, at_least: null, id: "return_tail" },
                { feature: "oi_change_1h", measure: "value", at_most: 0, at_least: null, id: "oi_fell" },
              ],
            }
          : {},
    },
    evidence_counts: {},
    evidence: [
      { class: "human_hypothesis", ref: "x", text: "t" },
      { class: "prospective_registration", ref: "truffle-reg-v1", hypothesis_id: "T-LONG" },
    ],
    links: {},
    limitations: ["No independent prospective evidence exists for this specification."],
    chain_problems: [],
    ...extra,
  };
}

export const TRUFFLE_REGISTRATION: TruffleRegistrationView = {
  registration_id: "truffle-reg-v1",
  registration_hash: "f".repeat(64),
  registered_at: "2026-10-25T01:50:00+00:00",
  question: "Do qualified Truffles show a different market-relative return than the eligible universe?",
  claims: [
    {
      hypothesis_id: "T-LONG",
      direction: "long",
      statement: "Long Truffles beat the eligible universe",
      ranking_candidate: "long-oi-v1",
      ranking_spec_hash: LIVE_HASH,
      qualification_id: "long-q-v1",
      qualification_spec_hash: "9".repeat(64),
      primary_label: "forward_relative_return_1h",
      secondary_labels: ["forward_relative_return_4h"],
    },
    {
      hypothesis_id: "T-SHORT",
      direction: "short",
      statement: "Short Truffles beat the eligible universe",
      ranking_candidate: "short-oi-v1",
      ranking_spec_hash: "8".repeat(64),
      qualification_id: "short-q-v1",
      qualification_spec_hash: "7".repeat(64),
      primary_label: "forward_relative_return_1h",
      secondary_labels: ["forward_relative_return_4h"],
    },
  ],
  plan: { primary_reference: "the same-timestamp eligible universe", multiplicity: { method: "Bonferroni", family_size: 2, adjusted_confidence: 0.95 } },
  limitations: ["Observational."],
  partitions: [
    { role: "validation", start: "2026-12-27", end: "2027-01-26", lifecycle: "registered_awaiting_data", eligible_from: "2027-01-27T04:00:00+00:00", days: 30, days_with_shard: 0, decisions: {} },
    { role: "holdout", start: "2027-01-27", end: "2027-02-26", lifecycle: "registered_awaiting_data", eligible_from: "2027-02-27T04:00:00+00:00", days: 30, days_with_shard: 0, decisions: {} },
  ],
};

export const MODEL_KNOWLEDGE: ModelKnowledge = {
  models: [
    ledgerEntry("long-oi-v1", "ranking_model", "registered_for_prospective_evaluation"),
    ledgerEntry("long-q-v1", "qualification_policy", "experimental"),
  ],
  truffle_registrations: [TRUFFLE_REGISTRATION],
  issues: [],
};

/** What `liveModels` returns when Sniffer ranks with `long-oi-v1` only. */
export const LIVE_LONG_ONLY = {
  status: "ok" as const,
  models: [{ direction: "long" as const, candidate_id: "long-oi-v1", spec_hash: LIVE_HASH }],
  unavailableDirections: [] as ("long" | "short")[],
};
