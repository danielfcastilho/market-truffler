import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ModelKnowledge, ModelKnowledgeEntry } from "@/lib/research";

import { ModelKnowledgeView } from "./model-knowledge";

function entry(id: string, kind: ModelKnowledgeEntry["kind"], status: string, extra: Partial<ModelKnowledgeEntry> = {}): ModelKnowledgeEntry {
  return {
    kind,
    id,
    revision: 2,
    revisions: 2,
    derived_at: "2026-10-25T01:50:00+00:00",
    content_hash: "c".repeat(64),
    status: status as ModelKnowledgeEntry["status"],
    status_basis: [],
    identity: {
      kind,
      id,
      spec_hash: "a".repeat(64),
      spec_version: 1,
      direction: "long",
      features: ["return_1h", "oi_change_1h"],
      model_family: kind === "ranking_model" ? "linear_weighted_v1" : undefined,
      applies_to: kind === "qualification_policy" ? { ranking_model: "long-m-v1", direction: "long" } : undefined,
      parameters: {},
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

const DATA: ModelKnowledge = {
  models: [
    entry("long-m-v1", "ranking_model", "registered_for_prospective_evaluation"),
    entry("long-q-v1", "qualification_policy", "experimental", { chain_problems: ["revision 2: breaks the revision chain"] }),
  ],
  truffle_registrations: [
    {
      registration_id: "truffle-reg-v1",
      registration_hash: "b".repeat(64),
      registered_at: "2026-10-25T01:50:00+00:00",
      question: "Do Truffles differ?",
      claims: [
        {
          hypothesis_id: "T-LONG",
          direction: "long",
          statement: "s",
          ranking_candidate: "long-m-v1",
          ranking_spec_hash: "a".repeat(64),
          qualification_id: "long-q-v1",
          qualification_spec_hash: "d".repeat(64),
          primary_label: "forward_relative_return_1h",
          secondary_labels: ["forward_relative_return_4h"],
        },
      ],
      plan: { primary_reference: "the same-timestamp eligible universe", multiplicity: { method: "Bonferroni", family_size: 2, adjusted_confidence: 0.95 } },
      limitations: ["Observational."],
      partitions: [
        { role: "validation", start: "2026-12-27", end: "2027-01-26", lifecycle: "registered_awaiting_data", eligible_from: "2027-01-26T00:00:00+00:00", days: 30, days_with_shard: 0, decisions: {} },
        { role: "holdout", start: "2027-01-27", end: "2027-02-26", lifecycle: "evaluated", eligible_from: "2027-02-26T00:00:00+00:00", days: 30, days_with_shard: 30, decisions: { "T-LONG": "insufficient_evidence" } },
      ],
    },
  ],
  issues: [],
};

describe("ModelKnowledgeView", () => {
  it("states that registration is not validation and marks unvalidated statuses", () => {
    render(<ModelKnowledgeView knowledge={DATA} />);
    expect(screen.getByText("Registering a model is not validating it.")).toBeInTheDocument();
    const model = document.querySelector('[data-model="long-m-v1"]') as HTMLElement;
    expect(within(model).getByText("Registered — awaiting prospective evidence")).toBeInTheDocument();
    expect(within(model).queryByText("human hypothesis")).toBeNull();
    expect(model).toHaveTextContent("prospective registration · truffle-reg-v1");
    const policy = document.querySelector('[data-model="long-q-v1"]') as HTMLElement;
    expect(within(policy).getByText("Experimental")).toBeInTheDocument();
    expect(within(policy).getByRole("alert")).toHaveTextContent("breaks the revision chain");
  });

  it("shows each partition's lifecycle, eligibility and recorded decisions only", () => {
    render(<ModelKnowledgeView knowledge={DATA} />);
    const rows = document.querySelectorAll("[data-lifecycle]");
    expect(rows[0]).toHaveTextContent("Registered — awaiting data");
    expect(rows[0]).toHaveTextContent("eligible from");
    expect(rows[0]).toHaveTextContent("—");
    expect(rows[1]).toHaveTextContent("T-LONG: insufficient_evidence");
    expect(screen.getByText(/Bonferroni over 2 primary claims/)).toBeInTheDocument();
  });

  it("says so when nothing is registered", () => {
    render(<ModelKnowledgeView knowledge={{ models: [], truffle_registrations: [], issues: [] }} />);
    expect(screen.getByText("No Truffle evaluation is registered.")).toBeInTheDocument();
  });
});
