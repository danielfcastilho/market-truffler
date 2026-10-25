import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { describeTerm, type RankingRun } from "@/lib/research";
import type { LiveModels } from "@/lib/research-questions";

import { ResearchModels } from "./research-models";
import { ResearchNav, activeResearchSection } from "./research-nav";
import {
  LIVE_HASH,
  LIVE_LONG_ONLY,
  MODEL_KNOWLEDGE,
  RANKING_RESEARCH,
  STUDY_RUN,
  ledgerEntry,
} from "./test-fixtures";

const pathname = vi.hoisted(() => ({ value: "/oink-corp/models" }));
const redirect = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ usePathname: () => pathname.value, redirect }));

const OK = <T,>(data: T) => ({ status: "ok" as const, data });

function renderModels(
  ranking = RANKING_RESEARCH,
  knowledge = MODEL_KNOWLEDGE,
  live: LiveModels = LIVE_LONG_ONLY,
) {
  return render(<ResearchModels ranking={OK(ranking)} knowledge={OK(knowledge)} live={live} />);
}

const card = (id: string) => document.querySelector(`[data-model="${id}"]`) as HTMLElement;

describe("ResearchModels", () => {
  it("marks the live model by id and specification, and keeps live apart from validated", () => {
    renderModels();
    expect(within(card("long-oi-v1")).getByText("Live in Sniffer — experimental model")).toBeInTheDocument();
    expect(within(card("long-oi-v1")).getByText("Registered — awaiting prospective evidence")).toBeInTheDocument();
    expect(within(card("long-price-v1")).getByText("Not used by Sniffer")).toBeInTheDocument();
    // The live direction is long only: a short model can't be verified either way.
    expect(within(card("short-oi-v1")).getByText("Not used by Sniffer")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/none of this is a live ranking/i);
    expect(screen.getByText(/research never changes a live ranking — and being live says nothing/)).toBeInTheDocument();
    // Live models come first.
    expect(document.querySelectorAll("[data-model]")[0]).toHaveAttribute("data-model", "long-oi-v1");
  });

  it("never marks a model live it cannot verify", () => {
    renderModels(RANKING_RESEARCH, MODEL_KNOWLEDGE, { status: "unknown", reason: "live rankings unavailable (HTTP 503)" });
    expect(screen.getByRole("status")).toHaveTextContent("Live status cannot be verified right now (live rankings unavailable (HTTP 503))");
    expect(screen.queryByText("Live in Sniffer — experimental model")).not.toBeInTheDocument();
    expect(within(card("long-oi-v1")).getByText("Live status unknown")).toBeInTheDocument();
  });

  it("flags a live model whose recorded specification differs", () => {
    const changed = { ...LIVE_LONG_ONLY, models: [{ ...LIVE_LONG_ONLY.models[0], spec_hash: "0".repeat(64) }] };
    renderModels(RANKING_RESEARCH, MODEL_KNOWLEDGE, changed);
    expect(within(card("long-oi-v1")).getByText("Sniffer uses a different specification under this name")).toBeInTheDocument();
  });

  it("explains each formula in words, with the raw specification in technical details", () => {
    renderModels();
    const model = within(card("long-oi-v1"));
    expect(model.getByText("−0.5 × percentile rank within the frame of 1-hour open interest change")).toBeInTheDocument();
    expect(model.getByText("Technical details")).toBeInTheDocument();
    expect(model.getByText(/-0.5 × cs_percentile\(oi_change_1h\)/)).toBeInTheDocument();
  });

  it("presents a study next to its model: compared with what, insufficient data, descriptive values", () => {
    renderModels();
    const study = within(document.getElementById("long-oi-vs-price-v1")!);
    expect(study.getByText("Compared with").nextSibling).toHaveTextContent("Long — price reversal (price only)");
    expect(study.getAllByText("Insufficient data").length).toBeGreaterThan(0);
    expect(study.getByText("1 of 10 qualifying days in segment")).toBeInTheDocument();
    expect(study.getByText("Observed values — descriptive only, not evidence")).toBeInTheDocument();
    expect(study.getByText("Difference (candidate − reference)").nextSibling).toHaveTextContent("−0.094 pp");
    expect(study.getByText(/no interval — fewer than 10 qualifying days/)).toBeInTheDocument();
    expect(within(card("long-price-v1")).getByText(/Used as the simpler reference in/)).toBeInTheDocument();
    expect(within(document.getElementById("short-oi-vs-price-v1")!).getByText("Registered but never run — no evidence exists.")).toBeInTheDocument();
  });

  it("shows a recorded interval for an evidence verdict, marks repeats, and states failures", () => {
    const positive: RankingRun = {
      ...STUDY_RUN,
      repeat_of: ["long-oi-vs-price-v1/earlier"],
      report: {
        ...STUDY_RUN.report!,
        primary: {
          ...STUDY_RUN.report!.primary,
          verdict: "positive_evidence",
          verdict_reasons: [],
          uncertainty: { suppressed: false, confidence_level: 0.9, interval: [0.0004, 0.0021], method: "day-block bootstrap" },
        },
        verdict: "positive_evidence",
      },
    };
    const { unmount } = renderModels({ ...RANKING_RESEARCH, studies: [{ ...RANKING_RESEARCH.studies[0], runs: [positive] }] });
    expect(screen.getByText(/90% interval \[\+0.040 pp, \+0.210 pp\]/)).toBeInTheDocument();
    expect(screen.queryByText("Observed values — descriptive only, not evidence")).not.toBeInTheDocument();
    expect(screen.getByText(/repeat of 1 earlier run\(s\) — not new evidence/)).toBeInTheDocument();
    expect(screen.getByText("Development data (exploratory)", { exact: false })).toBeInTheDocument();
    unmount();
    const failed = { ...STUDY_RUN, status: "failed", error: "ExperimentSpecError: reserved", report: undefined };
    renderModels({ ...RANKING_RESEARCH, studies: [{ ...RANKING_RESEARCH.studies[0], runs: [failed] }] });
    expect(screen.getByText(/No evaluation was produced: ExperimentSpecError: reserved/)).toBeInTheDocument();
  });

  it("explains Truffle rules in words, as separate from the ranking, with their evaluation", () => {
    renderModels();
    expect(screen.getByText(/It never changes a score or an order/)).toBeInTheDocument();
    const policy = within(card("long-q-v1"));
    expect(policy.getByText("1-hour return is in the bottom 5% of instruments in the same frame")).toBeInTheDocument();
    expect(policy.getByText("1-hour Open Interest change is at most 0")).toBeInTheDocument();
    const rows = document.querySelectorAll("[data-lifecycle]");
    expect(rows[0]).toHaveTextContent("Registered — awaiting data");
    expect(rows[0]).toHaveTextContent("eligible from");
    expect(screen.getByText(/Bonferroni over 2 primary claims/)).toBeInTheDocument();
  });

  it("shows ledger integrity problems and unavailable sources", () => {
    const broken = { ...MODEL_KNOWLEDGE, models: [ledgerEntry("long-oi-v1", "ranking_model", "experimental", { chain_problems: ["revision 2: breaks the revision chain"] })] };
    const { unmount } = renderModels(RANKING_RESEARCH, broken);
    expect(within(card("long-oi-v1")).getByRole("alert")).toHaveTextContent("breaks the revision chain");
    unmount();
    render(
      <ResearchModels
        ranking={{ status: "unavailable", message: "HTTP 500" }}
        knowledge={OK({ models: [], truffle_registrations: [], issues: [] })}
        live={LIVE_LONG_ONLY}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Ranking studies: HTTP 500");
    expect(screen.getByText("No ranking model is recorded yet.")).toBeInTheDocument();
    expect(screen.getByText("No Truffle evaluation is registered.")).toBeInTheDocument();
  });

  it("keeps a model's live status tied to its recorded hash, not the study candidate's name", () => {
    const ledgerOnly = { ...MODEL_KNOWLEDGE, models: [ledgerEntry("long-oi-v1", "ranking_model", "experimental", { identity: { ...ledgerEntry("long-oi-v1", "ranking_model", "x").identity, spec_hash: LIVE_HASH } })] };
    renderModels({ candidates: [], studies: [], issues: [] }, ledgerOnly);
    expect(within(card("long-oi-v1")).getByText("Live in Sniffer — experimental model")).toBeInTheDocument();
    expect(within(card("long-oi-v1")).getByText(/Specification file not found/)).toBeInTheDocument();
  });
});

describe("ResearchNav", () => {
  it("has four sections and the audit log as a secondary link", () => {
    pathname.value = "/oink-corp/models";
    render(<ResearchNav />);
    expect(screen.getByRole("link", { name: "Models" })).toHaveAttribute("aria-current", "page");
    for (const name of ["Overview", "Hypotheses", "Data readiness", "Audit log"]) {
      expect(screen.getByRole("link", { name })).not.toHaveAttribute("aria-current");
    }
    expect(screen.getByRole("link", { name: "Audit log" })).toHaveAttribute("href", "/oink-corp/history");
  });

  it("keeps existing deep links in their section", () => {
    expect(activeResearchSection("/oink-corp")).toBe("/oink-corp");
    expect(activeResearchSection("/oink-corp/campaigns/x")).toBe("/oink-corp/hypotheses");
    expect(activeResearchSection("/oink-corp/experiments/y")).toBe("/oink-corp/hypotheses");
    expect(activeResearchSection("/oink-corp/ranking")).toBe("/oink-corp/models");
    expect(activeResearchSection("/oink-corp/data-health")).toBe("/oink-corp/data-health");
    expect(activeResearchSection("/oink-corp/history")).toBeNull();
  });

  it("redirects the retired ranking page to Models, where each study keeps its anchor", async () => {
    const { default: RankingPage } = await import("@/app/(protected)/oink-corp/ranking/page");
    RankingPage();
    expect(redirect).toHaveBeenCalledWith("/oink-corp/models");
  });
});

describe("describeTerm", () => {
  it("states sign, weight, transform and feature", () => {
    expect(describeTerm({ feature: "return_1h", transform: "cs_robust_z", weight: 2 })).toBe(
      "+2 × robust z-score within the frame of 1-hour return",
    );
  });
});
