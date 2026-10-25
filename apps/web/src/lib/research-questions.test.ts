import { describe, expect, it } from "vitest";

import {
  CAMPAIGN,
  CAMPAIGN_DETAIL,
  LIVE_HASH,
  MODEL_KNOWLEDGE,
  OVERVIEW,
  RANKING_RESEARCH,
  STUDY_RUN,
} from "@/components/research/test-fixtures";
import type { LiveRankings } from "@/lib/rankings";
import {
  deriveQuestions,
  describeBlocker,
  liveModels,
  liveState,
  parseBlocker,
  scientificSummary,
  tierOf,
  type ResearchQuestion,
} from "@/lib/research-questions";

const ALL = { overview: OVERVIEW, campaignDetails: { [CAMPAIGN]: CAMPAIGN_DETAIL }, ranking: RANKING_RESEARCH, models: MODEL_KNOWLEDGE };

describe("deriveQuestions", () => {
  it("poses one question per recorded hypothesis, ranking study and prospective registration", () => {
    const questions = deriveQuestions(ALL);
    expect(questions.map((q) => q.key)).toEqual([
      `hypothesis:${CAMPAIGN}/H1`,
      `hypothesis:${CAMPAIGN}/H3`,
      "study:long-oi-vs-price-v1",
      "study:short-oi-vs-price-v1",
      "registration:truffle-reg-v1",
    ]);
    const [h1, , study, unrun, registration] = questions;
    expect(h1.title).toBe("Price ↓ + Open Interest ↓ · 1h");
    expect(h1.context).toBe("Does Open Interest add information beyond price?");
    expect(h1.href).toBe(`/oink-corp/campaigns/${CAMPAIGN}#H1`);
    expect(h1.tier).toBe("insufficient");
    expect(study.title).toBe("Long: does Open Interest improve a price-reversal ranking?");
    expect(study.href).toBe("/oink-corp/models#long-oi-vs-price-v1");
    expect(study.tier).toBe("insufficient");
    expect(unrun.tier).toBe("not_evaluated");
    expect(registration.direction).toBe("both");
    expect(registration.tier).toBe("awaiting");
    expect(registration.independentTest).toMatch(/evaluable from 2027-01-27 at the earliest, if enough data is collected/);
  });

  it("reads blockers from the recorded ledger limitations and verdict reasons", () => {
    const [h1, h3, study] = deriveQuestions(ALL);
    expect(h1.blockers.map(describeBlocker)).toEqual([
      "1 of 10 qualifying days in segment",
      "No validation run: nothing is independently confirmed.",
    ]);
    expect(h3.blockers).toEqual([]); // no ledger entry: nothing invented
    expect(study.blockers.map(describeBlocker)).toEqual([
      "1 of 10 qualifying days in segment",
      "24 of 200 evaluated timestamps",
    ]);
  });

  it("states the registered independent test as a plan, never a date it will happen", () => {
    const [h1] = deriveQuestions(ALL);
    expect(h1.independentTest).toMatch(/^Prospective validation registered for 2026-10-26 → 2026-11-25; evaluable from/);
    expect(h1.independentTest).toMatch(/at the earliest, if enough data is collected/);
  });

  it("lists a record once, but never merges distinct hypotheses that read alike", () => {
    const [campaign] = OVERVIEW.campaigns;
    const twin = { ...campaign.hypotheses[0], hypothesis_id: "H9" };
    const overview = { ...OVERVIEW, campaigns: [campaign, { ...campaign, hypotheses: [campaign.hypotheses[0], twin] }] };
    const ranking = { ...RANKING_RESEARCH, studies: [RANKING_RESEARCH.studies[0], RANKING_RESEARCH.studies[0]] };
    const questions = deriveQuestions({ overview, ranking, models: null });
    const titles = questions.filter((q) => q.title === "Price ↓ + Open Interest ↓ · 1h").map((q) => q.key);
    expect(titles).toEqual([`hypothesis:${CAMPAIGN}/H1`, `hypothesis:${CAMPAIGN}/H9`]);
    expect(questions.filter((q) => q.kind === "ranking_study")).toHaveLength(1);
  });

  it("marks a failed run as failed with its recorded error, not as a result", () => {
    const failed = { ...STUDY_RUN, status: "failed", error: "ExperimentSpecError: reserved", report: undefined };
    const ranking = { ...RANKING_RESEARCH, studies: [{ ...RANKING_RESEARCH.studies[0], runs: [failed] }] };
    const [q] = deriveQuestions({ overview: null, ranking, models: null });
    expect(q.tier).toBe("failed");
    expect(q.blockers[0].text).toBe("ExperimentSpecError: reserved");
  });
});

describe("evidence tiers", () => {
  it("keeps the backend's distinctions", () => {
    expect(tierOf({ type: "knowledge", status: "insufficient_evidence" })).toBe("insufficient");
    expect(tierOf({ type: "knowledge", status: "inconclusive" })).toBe("inconclusive");
    expect(tierOf({ type: "knowledge", status: "preliminary_support" })).toBe("exploratory");
    expect(tierOf({ type: "knowledge", status: "independently_supported" })).toBe("validated");
    expect(tierOf({ type: "knowledge", status: "conflicting_evidence" })).toBe("conflicting");
    expect(tierOf({ type: "knowledge", status: "untested" })).toBe("not_evaluated");
    expect(tierOf({ type: "knowledge", status: null })).toBe("unknown");
    // A ranking study is development-only: a directional verdict is exploratory.
    expect(tierOf({ type: "verdict", verdict: "positive_evidence", failed: false })).toBe("exploratory");
    expect(tierOf({ type: "verdict", verdict: "insufficient_data", failed: false })).toBe("insufficient");
    expect(tierOf({ type: "verdict", verdict: null, failed: false })).toBe("not_evaluated");
    expect(tierOf({ type: "registration", lifecycle: "evaluated", decisions: { T: "x" } })).toBe("prospective_decision");
  });

  it("parses the evaluator's reason form and keeps anything else verbatim", () => {
    expect(parseBlocker("development: 1 qualifying days in segment (< 10)")).toMatchObject({
      have: 1,
      need: 10,
      what: "qualifying days in segment",
    });
    expect(parseBlocker("interval, day consistency, or concentration criteria not met")).toEqual({
      text: "interval, day consistency, or concentration criteria not met",
    });
  });
});

function question(tier: ResearchQuestion["tier"], kind: ResearchQuestion["kind"] = "hypothesis"): ResearchQuestion {
  return {
    key: `${kind}:${tier}:${Math.random()}`,
    kind,
    title: tier,
    context: null,
    direction: "long",
    status: { type: "knowledge", status: null },
    tier,
    blockers: [],
    independentTest: null,
    href: "/",
    source: "s",
  };
}

describe("scientificSummary", () => {
  it("says no conclusion is possible when everything lacks data, and aggregates the recorded blockers", () => {
    const summary = scientificSummary(deriveQuestions(ALL));
    expect(summary.headline).toBe("No research question has enough data for a conclusion yet.");
    expect(summary.breakdown).toEqual([
      "Insufficient data: 2 hypotheses, 1 ranking study",
      "Not evaluated: 1 ranking study",
      "Registered, awaiting data: 1 prospective evaluation",
    ]);
    expect(summary.blockers).toEqual([
      "qualifying days in segment: 1 recorded, 10 required",
      "evaluated timestamps: 24 recorded, 200 required",
    ]);
  });

  it("never calls exploratory evidence validated", () => {
    const summary = scientificSummary([question("exploratory"), question("exploratory"), question("insufficient")]);
    expect(summary.headline).toBe("2 research questions have exploratory evidence; none is independently validated.");
  });

  it("counts validated evidence against evaluable questions only", () => {
    const summary = scientificSummary([
      question("validated"),
      question("insufficient"),
      question("awaiting", "prospective_registration"),
    ]);
    expect(summary.headline).toBe("1 of 2 research questions have independently validated evidence.");
  });

  it("separates inconclusive from insufficient", () => {
    expect(scientificSummary([question("inconclusive"), question("insufficient")]).headline).toMatch(
      /inconclusive or not yet sufficient/,
    );
  });

  it("names sources it could not load instead of concluding anything from them", () => {
    const partial = scientificSummary(deriveQuestions({ ...ALL, ranking: null }), ["ranking studies"]);
    expect(partial.missing).toEqual(["ranking studies"]);
    const none = scientificSummary([], ["market hypotheses", "ranking studies"]);
    expect(none.headline).toBe("Research records could not be loaded, so the state of research cannot be summarised.");
    expect(scientificSummary([]).headline).toBe("No research question has been recorded yet.");
  });
});

function rankings(long: { candidate_id: string; spec_hash: string } | null, short: { candidate_id: string; spec_hash: string } | null) {
  const direction = (model: typeof long, d: "long" | "short") =>
    model === null ? null : ({ direction: d, available: true, reason: null, model: { ...model, direction: d } } as unknown as LiveRankings["long"]);
  return { status: "ok" as const, data: { available: true, reason: null, frame: null, long: direction(long, "long"), short: direction(short, "short") } as LiveRankings };
}

describe("live model identification", () => {
  it("matches id and specification hash, never a name", () => {
    const live = liveModels(rankings({ candidate_id: "long-oi-v1", spec_hash: LIVE_HASH }, { candidate_id: "short-oi-v1", spec_hash: "8".repeat(64) }));
    expect(liveState({ id: "long-oi-v1", direction: "long", specHash: LIVE_HASH }, live)).toBe("live");
    expect(liveState({ id: "long-oi-v1", direction: "long", specHash: "0".repeat(64) }, live)).toBe("spec_differs");
    expect(liveState({ id: "long-price-v1", direction: "long", specHash: "c".repeat(64) }, live)).toBe("not_live");
    expect(liveState({ id: "long-oi-v1", direction: "long", specHash: null }, live)).toBe("unknown");
  });

  it("is unknown when live rankings or one direction cannot be read", () => {
    const failed = liveModels({ status: "error", message: "HTTP 503" });
    expect(failed).toEqual({ status: "unknown", reason: "live rankings unavailable (HTTP 503)" });
    expect(liveState({ id: "long-oi-v1", direction: "long", specHash: LIVE_HASH }, failed)).toBe("unknown");
    const longOnly = liveModels(rankings({ candidate_id: "long-oi-v1", spec_hash: LIVE_HASH }, null));
    expect(liveState({ id: "short-oi-v1", direction: "short", specHash: "8".repeat(64) }, longOnly)).toBe("unknown");
    expect(liveState({ id: "long-price-v1", direction: "long", specHash: "c".repeat(64) }, longOnly)).toBe("not_live");
    expect(liveModels(rankings(null, null)).status).toBe("unknown");
  });
});
