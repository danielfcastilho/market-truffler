/**
 * Research questions and the scientific state of OINK CORP, derived from
 * recorded research only: campaign hypotheses (knowledge ledgers), ranking
 * studies (their latest run's verdict) and prospective Truffle
 * registrations (their partitions' lifecycle). Nothing is invented: a
 * question exists only when a record poses it, a status is only ever a
 * recorded one, and a blocker is a recorded verdict reason or ledger
 * limitation — parsed when it has the evaluator's "N what (< M)" form,
 * shown verbatim when it does not.
 *
 * Pure functions, no fetching: the pages pass in whatever the API
 * returned, and a source that could not be loaded is named in the summary
 * instead of being treated as "nothing found".
 */
import type { LiveRankings } from "@/lib/rankings";
import {
  hypothesisTitle,
  type CampaignDetail,
  type Direction,
  type EvaluationSummary,
  type KnowledgeStatus,
  type ModelKnowledge,
  type RankingResearch,
  type RankingRun,
  type ResearchOverview,
  type TruffleRegistrationView,
  type Verdict,
} from "@/lib/research";

/** How far the recorded evidence for a question goes — a coarse grouping of
 * the backend's own statuses, never a new judgement. */
export type EvidenceTier =
  | "validated" // independently validated (prospective or untouched validation data)
  | "conflicting" // validation evidence disagrees
  | "prospective_decision" // a prospective evaluation recorded a decision
  | "exploratory" // a directional development verdict / preliminary knowledge
  | "inconclusive"
  | "insufficient" // not enough data for any conclusion
  | "awaiting" // a prospective evaluation is registered, nothing evaluated yet
  | "not_evaluated" // posed, never run
  | "failed" // the latest run produced no evaluation
  | "unknown"; // no ledger revision covers it yet

export type QuestionStatus =
  | { type: "knowledge"; status: KnowledgeStatus | null }
  | { type: "verdict"; verdict: Verdict | null; failed: boolean }
  | { type: "registration"; lifecycle: string; decisions: Record<string, string> };

/** A recorded reason a conclusion is not possible. */
export interface Blocker {
  text: string;
  /** Present when the reason has the evaluator's "N what (< M)" form. */
  have?: number;
  need?: number;
  what?: string;
}

export interface ResearchQuestion {
  /** Unique per scientific question and its record, e.g. "study:long-oi-vs-price-v1". */
  key: string;
  kind: "hypothesis" | "ranking_study" | "prospective_registration";
  title: string;
  /** The broader question the record belongs to (a campaign's question). */
  context: string | null;
  direction: Direction | "both" | null;
  status: QuestionStatus;
  tier: EvidenceTier;
  blockers: Blocker[];
  /** A registered independent test, worded as a plan — never a promise. */
  independentTest: string | null;
  href: string;
  /** Where the question is recorded, for traceability. */
  source: string;
}

// -- tiers --------------------------------------------------------------------------------------

const KNOWLEDGE_TIER: Record<KnowledgeStatus, EvidenceTier> = {
  untested: "not_evaluated",
  insufficient_evidence: "insufficient",
  inconclusive: "inconclusive",
  preliminary_support: "exploratory",
  preliminary_contradiction: "exploratory",
  preliminary_difference: "exploratory",
  independently_supported: "validated",
  independently_contradicted: "validated",
  independently_observed_difference: "validated",
  conflicting_evidence: "conflicting",
};

export function tierOf(status: QuestionStatus): EvidenceTier {
  if (status.type === "knowledge") {
    return status.status ? (KNOWLEDGE_TIER[status.status] ?? "unknown") : "unknown";
  }
  if (status.type === "verdict") {
    if (status.failed) return "failed";
    if (status.verdict === null) return "not_evaluated";
    if (status.verdict === "insufficient_data") return "insufficient";
    if (status.verdict === "inconclusive") return "inconclusive";
    return "exploratory"; // ranking studies are development-only
  }
  return Object.keys(status.decisions).length > 0 ? "prospective_decision" : "awaiting";
}

export const TIER_LABEL: Record<EvidenceTier, string> = {
  validated: "independently validated",
  conflicting: "conflicting validation evidence",
  prospective_decision: "prospective evaluation decided",
  exploratory: "exploratory evidence only",
  inconclusive: "inconclusive",
  insufficient: "insufficient data",
  awaiting: "registered, awaiting data",
  not_evaluated: "not evaluated",
  failed: "evaluation failed",
  unknown: "status not yet recorded",
};

// -- blockers -----------------------------------------------------------------------------------

const REASON = /^(\d+) (.+?) \(< (\d+)\)$/;

/** One recorded reason, parsed when it has the evaluator's form. A ledger
 * limitation's "segment: " prefix is kept in the text but not in `what`. */
export function parseBlocker(text: string): Blocker {
  const body = text.replace(/^(development|validation): /, "");
  const match = REASON.exec(body.trim());
  if (!match) return { text };
  return { text, have: Number(match[1]), what: match[2], need: Number(match[3]) };
}

/** "1 of 10 qualifying days in segment", or the recorded text itself. */
export function describeBlocker(blocker: Blocker): string {
  if (blocker.have === undefined || blocker.need === undefined || !blocker.what) return blocker.text;
  return `${blocker.have} of ${blocker.need} ${blocker.what}`;
}

// -- derivation ---------------------------------------------------------------------------------

function latestLedgerHypothesis(detail: CampaignDetail | null | undefined, hypothesisId: string) {
  const revisions = detail?.knowledge_revisions ?? [];
  const latest = revisions.reduce<(typeof revisions)[number] | null>(
    (best, r) => (best === null || r.revision > best.revision ? r : best),
    null,
  );
  return latest?.hypotheses.find((h) => h.hypothesis_id === hypothesisId) ?? null;
}

/** A campaign's registered prospective validation for one hypothesis, as a plan. */
export function independentTestFor(
  evaluations: EvaluationSummary[],
  hypothesisId: string | null,
): string | null {
  const plans = evaluations.filter((e) => hypothesisId === null || e.hypotheses.includes(hypothesisId));
  const validation = plans.flatMap((e) => e.partitions).find((p) => p.role === "validation");
  if (!validation) return null;
  if (validation.state === "awaiting_data") {
    return (
      `Prospective validation registered for ${validation.start} → ${validation.end}; ` +
      `evaluable from ${validation.eligible_from.slice(0, 10)} at the earliest, if enough data is collected.`
    );
  }
  if (validation.state === "completed") return "Prospective validation completed — see the evidence.";
  return `Prospective validation: ${validation.state.replaceAll("_", " ")}.`;
}

function fromHypotheses(
  overview: ResearchOverview,
  details: Record<string, CampaignDetail | null>,
): ResearchQuestion[] {
  return overview.campaigns.flatMap((campaign) =>
    campaign.hypotheses.map((h) => {
      const ledger = latestLedgerHypothesis(details[campaign.campaign_id], h.hypothesis_id);
      const status: QuestionStatus = { type: "knowledge", status: h.knowledge_status };
      return {
        key: `hypothesis:${campaign.campaign_id}/${h.hypothesis_id}`,
        kind: "hypothesis" as const,
        title: h.conditions.length ? hypothesisTitle(h.conditions) : h.experiment_id,
        context: campaign.question,
        direction: h.direction,
        status,
        tier: tierOf(status),
        blockers: (ledger?.limitations ?? []).map(parseBlocker),
        independentTest: independentTestFor(campaign.evaluations, h.hypothesis_id),
        href: `/oink-corp/campaigns/${campaign.campaign_id}#${h.hypothesis_id}`,
        source: `Campaign ${campaign.campaign_id} · ${h.hypothesis_id}`,
      };
    }),
  );
}

export function latestRankingRun(runs: RankingRun[]): RankingRun | null {
  return runs.find((r) => r.status === "completed") ?? runs[0] ?? null;
}

function fromStudies(research: RankingResearch): ResearchQuestion[] {
  return research.studies.map((view) => {
    const run = latestRankingRun(view.runs);
    const failed = run !== null && (run.status !== "completed" || !run.report);
    const verdict = run?.report?.primary.verdict ?? null;
    const status: QuestionStatus = { type: "verdict", verdict: failed ? null : verdict, failed };
    const reasons = failed ? [run?.error ?? "the run produced no evaluation"] : (run?.report?.primary.verdict_reasons ?? []);
    return {
      key: `study:${view.study.study_id}`,
      kind: "ranking_study" as const,
      title: view.study.name,
      context: view.study.question,
      direction: view.candidate?.direction ?? null,
      status,
      tier: tierOf(status),
      blockers: reasons.map(parseBlocker),
      independentTest: null,
      href: `/oink-corp/models#${view.study.study_id}`,
      source: `Ranking study ${view.study.study_id}`,
    };
  });
}

function registrationLifecycle(registration: TruffleRegistrationView) {
  const validation = registration.partitions.find((p) => p.role === "validation") ?? registration.partitions[0];
  return validation ?? null;
}

function fromRegistrations(knowledge: ModelKnowledge): ResearchQuestion[] {
  return knowledge.truffle_registrations.map((registration) => {
    const partition = registrationLifecycle(registration);
    const decisions = Object.assign({}, ...registration.partitions.map((p) => p.decisions)) as Record<string, string>;
    const status: QuestionStatus = {
      type: "registration",
      lifecycle: partition?.lifecycle ?? "unknown",
      decisions,
    };
    const directions = new Set(registration.claims.map((c) => c.direction));
    return {
      key: `registration:${registration.registration_id}`,
      kind: "prospective_registration" as const,
      title: registration.question,
      context: null,
      direction: directions.size > 1 ? ("both" as const) : ([...directions][0] ?? null),
      status,
      tier: tierOf(status),
      blockers: [],
      independentTest:
        partition && partition.lifecycle === "registered_awaiting_data"
          ? `Prospective ${partition.role} registered for ${partition.start} → ${partition.end}; evaluable from ${partition.eligible_from.slice(0, 10)} at the earliest, if enough data is collected.`
          : null,
      href: `/oink-corp/models#${registration.registration_id}`,
      source: `Prospective registration ${registration.registration_id}`,
    };
  });
}

export interface QuestionSources {
  overview: ResearchOverview | null;
  campaignDetails?: Record<string, CampaignDetail | null>;
  ranking: RankingResearch | null;
  models: ModelKnowledge | null;
}

/** Every recorded research question, once each. Distinct records stay
 * distinct even when their titles read alike; the same record is listed
 * once even if a source repeats it. */
export function deriveQuestions(sources: QuestionSources): ResearchQuestion[] {
  const all = [
    ...(sources.overview ? fromHypotheses(sources.overview, sources.campaignDetails ?? {}) : []),
    ...(sources.ranking ? fromStudies(sources.ranking) : []),
    ...(sources.models ? fromRegistrations(sources.models) : []),
  ];
  const seen = new Set<string>();
  return all.filter((q) => (seen.has(q.key) ? false : (seen.add(q.key), true)));
}

// -- summary ------------------------------------------------------------------------------------

export interface ScientificSummary {
  headline: string;
  breakdown: string[];
  /** Recorded blockers across insufficient questions, aggregated by kind. */
  blockers: string[];
  /** Sources that could not be loaded and are therefore not summarised. */
  missing: string[];
}

const TIER_ORDER: EvidenceTier[] = [
  "validated",
  "prospective_decision",
  "conflicting",
  "exploratory",
  "inconclusive",
  "insufficient",
  "failed",
  "not_evaluated",
  "awaiting",
  "unknown",
];

const KIND_NOUN: Record<ResearchQuestion["kind"], [string, string]> = {
  hypothesis: ["hypothesis", "hypotheses"],
  ranking_study: ["ranking study", "ranking studies"],
  prospective_registration: ["prospective evaluation", "prospective evaluations"],
};

function countOf(questions: ResearchQuestion[], tier: EvidenceTier) {
  return questions.filter((q) => q.tier === tier).length;
}

function nouns(questions: ResearchQuestion[]): string {
  const parts = (Object.keys(KIND_NOUN) as ResearchQuestion["kind"][])
    .map((kind) => {
      const n = questions.filter((q) => q.kind === kind).length;
      return n ? `${n} ${KIND_NOUN[kind][n === 1 ? 0 : 1]}` : null;
    })
    .filter(Boolean);
  return parts.join(", ");
}

/** The current scientific state in a sentence, from the questions' recorded
 * statuses. Unloadable sources are named, never read as "no evidence". */
export function scientificSummary(questions: ResearchQuestion[], missing: string[] = []): ScientificSummary {
  const evaluable = questions.filter((q) => q.kind !== "prospective_registration");
  const validated = countOf(questions, "validated");
  const exploratory = countOf(questions, "exploratory");
  const inconclusive = countOf(questions, "inconclusive");
  const conflicting = countOf(questions, "conflicting");
  const decided = countOf(questions, "prospective_decision");

  let headline: string;
  if (questions.length === 0) {
    headline = missing.length
      ? "Research records could not be loaded, so the state of research cannot be summarised."
      : "No research question has been recorded yet.";
  } else if (validated > 0) {
    headline = `${validated} of ${evaluable.length} research questions have independently validated evidence.`;
  } else if (exploratory > 0) {
    headline = `${exploratory} research question${exploratory === 1 ? " has" : "s have"} exploratory evidence; none is independently validated.`;
  } else if (inconclusive + conflicting + decided > 0) {
    headline = "No research question has independently validated evidence; the evidence so far is inconclusive or not yet sufficient.";
  } else {
    headline = "No research question has enough data for a conclusion yet.";
  }

  const breakdown = TIER_ORDER.flatMap((tier) => {
    const matching = questions.filter((q) => q.tier === tier);
    return matching.length ? [`${TIER_LABEL[tier][0].toUpperCase()}${TIER_LABEL[tier].slice(1)}: ${nouns(matching)}`] : [];
  });

  const byWhat = new Map<string, { have: number[]; need: Set<number> }>();
  for (const q of questions.filter((q) => q.tier === "insufficient")) {
    for (const b of q.blockers) {
      if (b.what === undefined || b.have === undefined || b.need === undefined) continue;
      const entry = byWhat.get(b.what) ?? { have: [], need: new Set<number>() };
      entry.have.push(b.have);
      entry.need.add(b.need);
      byWhat.set(b.what, entry);
    }
  }
  const blockers = [...byWhat.entries()].map(([what, { have, need }]) => {
    const lo = Math.min(...have);
    const hi = Math.max(...have);
    const range = lo === hi ? `${lo}` : `${lo}–${hi}`;
    return `${what}: ${range} recorded, ${[...need].sort((a, b) => a - b).join(" or ")} required`;
  });

  return { headline, breakdown, blockers, missing };
}

// -- live models --------------------------------------------------------------------------------

export interface LiveIdentity {
  direction: Direction;
  candidate_id: string;
  spec_hash: string;
}

export type LiveRankingsInput = { status: "ok"; data: LiveRankings } | { status: "error"; message: string };

export type LiveModels =
  | { status: "ok"; models: LiveIdentity[]; unavailableDirections: Direction[] }
  | { status: "unknown"; reason: string };

/** Which models live Sniffer is using, from its own rankings response. */
export function liveModels(result: LiveRankingsInput | null): LiveModels {
  if (result === null) return { status: "unknown", reason: "live rankings were not requested" };
  if (result.status === "error") return { status: "unknown", reason: `live rankings unavailable (${result.message})` };
  const models: LiveIdentity[] = [];
  const unavailable: Direction[] = [];
  for (const direction of ["long", "short"] as const) {
    const model = result.data[direction]?.model;
    if (model) models.push({ direction, candidate_id: model.candidate_id, spec_hash: model.spec_hash });
    else unavailable.push(direction);
  }
  if (models.length === 0) {
    return { status: "unknown", reason: result.data.reason ?? "live rankings report no model" };
  }
  return { status: "ok", models, unavailableDirections: unavailable };
}

export type LiveState = "live" | "not_live" | "spec_differs" | "unknown";

/** Whether a model (by id and specification hash) is the one live Sniffer
 * uses. Never inferred from a name; "unknown" whenever it can't be verified. */
export function liveState(
  model: { id: string; direction: Direction | null; specHash: string | null },
  live: LiveModels,
): LiveState {
  if (live.status !== "ok") return "unknown";
  const match = live.models.find((m) => m.candidate_id === model.id);
  if (!match) {
    return model.direction && live.unavailableDirections.includes(model.direction) ? "unknown" : "not_live";
  }
  if (model.specHash === null) return "unknown";
  return match.spec_hash === model.specHash ? "live" : "spec_differs";
}
