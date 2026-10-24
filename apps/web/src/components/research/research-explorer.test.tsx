import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CampaignExplorer } from "./campaign-explorer";
import { ExperimentInspector } from "./experiment-inspector";
import { KnowledgeBadge } from "./knowledge-badge";
import { ResearchHistory } from "./research-history";
import { ResearchOverview } from "./research-overview";
import { ResearchUnavailable } from "./research-unavailable";
import { RunEvidence } from "./run-evidence";
import {
  CAMPAIGN,
  CAMPAIGN_DETAIL,
  CAND,
  COMPARISON_RUN,
  EXPERIMENT_DETAIL,
  HISTORY,
  INSUFFICIENT_RUN,
  LEDGER,
  OVERVIEW,
  PENDING_EVALUATION,
  POSITIVE_RUN,
} from "./test-fixtures";
import { VerdictBadge } from "./verdict-badge";

describe("badges", () => {
  it("uses exactly the backend verdicts, and marks failed runs", () => {
    const { rerender } = render(<VerdictBadge verdict="insufficient_data" />);
    expect(screen.getByText("Insufficient data")).toHaveAttribute("data-verdict", "insufficient_data");
    rerender(<VerdictBadge verdict={null} failed />);
    expect(screen.getByText("Run failed")).toBeInTheDocument();
  });

  it("names knowledge statuses separately from verdicts, and never invents one", () => {
    const { rerender } = render(<KnowledgeBadge status="insufficient_evidence" />);
    expect(screen.getByText("Insufficient evidence")).toHaveAttribute(
      "data-knowledge",
      "insufficient_evidence",
    );
    rerender(<KnowledgeBadge status={null} />);
    expect(screen.getByText("Not yet in the ledger")).toBeInTheDocument();
    rerender(<KnowledgeBadge status="proven" />);
    expect(screen.getByText("Not yet in the ledger")).toHaveAttribute("data-knowledge", "none");
  });
});

describe("RunEvidence", () => {
  it("presents insufficient data honestly: reasons, no interval, estimates labelled as observations", () => {
    render(<RunEvidence record={INSUFFICIENT_RUN} />);
    expect(screen.getByText("Insufficient data")).toBeInTheDocument();
    expect(screen.getByText(/Why no conclusion is possible/)).toBeInTheDocument();
    expect(screen.getByText("1 qualifying days in segment (< 10)")).toBeInTheDocument();
    expect(screen.getByText("Descriptive observations — not evidence")).toBeInTheDocument();
    expect(screen.getByText(/No uncertainty interval: fewer than 10 qualifying days/)).toBeInTheDocument();
    expect(screen.queryByText(/% interval of the spread/)).not.toBeInTheDocument();
    expect(screen.getByText(/not independent validation/)).toBeInTheDocument();
  });

  it("shows the recorded interval for an evidence verdict, without the observation caveat", () => {
    render(<RunEvidence record={POSITIVE_RUN} />);
    expect(screen.getByText(/90% interval of the spread/)).toHaveTextContent("+0.040 pp");
    expect(screen.queryByText("Descriptive observations — not evidence")).not.toBeInTheDocument();
  });

  it("shows a failed run's error instead of any figures", () => {
    render(<RunEvidence record={{ ...INSUFFICIENT_RUN, status: "failed", error: "OSError: gone" }} />);
    expect(screen.getByText(/No evaluation was produced: OSError: gone/)).toBeInTheDocument();
    expect(screen.queryByText("Timestamps with selections")).not.toBeInTheDocument();
  });

  it("names comparison figures against the reference group", () => {
    render(<RunEvidence record={COMPARISON_RUN} />);
    expect(screen.getByText("Reference group mean")).toBeInTheDocument();
  });
});

describe("ResearchOverview", () => {
  it("leads with knowledge, not execution", () => {
    render(<ResearchOverview overview={OVERVIEW} />);
    expect(screen.getByRole("heading", { name: "Research intelligence" })).toBeInTheDocument();
    expect(screen.getByText("Independently validated").nextSibling).toHaveTextContent("0");
    expect(screen.queryByText(/Established findings/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Current knowledge: 2 insufficient evidence/)).toBeInTheDocument();
    expect(screen.queryByText(COMPARISON_RUN.run_id)).not.toBeInTheDocument(); // no run ids here
  });

  it("groups each campaign's hypotheses by Long and Short with readable titles", () => {
    render(<ResearchOverview overview={OVERVIEW} />);
    const long = screen.getByText("long hypotheses").parentElement!;
    const short = screen.getByText("short hypotheses").parentElement!;
    const h1 = within(long).getByRole("link", { name: /Price ↓ \+ Open Interest ↓ · 1h/ });
    expect(h1).toHaveAttribute("href", `/oink-corp/campaigns/${CAMPAIGN}#H1`);
    expect(within(h1).getByText("Insufficient evidence")).toBeInTheDocument();
    expect(within(short).getByText("Price ↑ + Open Interest ↓ · 1h")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Explore campaign →" })).toHaveAttribute(
      "href",
      `/oink-corp/campaigns/${CAMPAIGN}`,
    );
  });

  it("summarises activity from artifacts and links to the full history", () => {
    render(<ResearchOverview overview={OVERVIEW} />);
    expect(screen.getByText("Knowledge ledger revision 1 for", { exact: false })).toBeInTheDocument();
    expect(screen.getByText(/H1: first recorded → Insufficient evidence/)).toBeInTheDocument();
    expect(screen.getByText(/1 repeat of earlier runs — not new evidence/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View research history →" })).toHaveAttribute(
      "href",
      "/oink-corp/history",
    );
  });

  it("flags a campaign without knowledge or with an inconsistent ledger", () => {
    const [campaign] = OVERVIEW.campaigns;
    render(
      <ResearchOverview
        overview={{
          ...OVERVIEW,
          campaigns: [
            { ...campaign, ledger_revision: null, ledger_problems: 2, hypotheses: campaign.hypotheses.map((h) => ({ ...h, knowledge_status: null })) },
          ],
        }}
      />,
    );
    expect(screen.getByText("Knowledge not yet derived for this campaign.")).toBeInTheDocument();
    expect(screen.getByText(/2 cited runs do not\s+match their records/)).toBeInTheDocument();
    expect(screen.getAllByText("Not yet in the ledger")).toHaveLength(2);
  });

  it("has an honest empty state", () => {
    render(
      <ResearchOverview
        overview={{
          ...OVERVIEW,
          counts: { ...OVERVIEW.counts, campaigns: 0, runs: 0, experiments_registered: 0 },
          campaigns: [],
          activity: [],
        }}
      />,
    );
    expect(screen.getByText("No research has been registered yet.")).toBeInTheDocument();
  });

  it("surfaces unreadable artifacts and API failures instead of hiding them", () => {
    const { unmount } = render(
      <ResearchOverview overview={{ ...OVERVIEW, issues: [{ artifact: "broken-v1.json", problem: "unreadable" }] }} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("broken-v1.json: unreadable");
    unmount();
    render(<ResearchUnavailable message="research API unreachable" />);
    expect(screen.getByRole("alert")).toHaveTextContent("Research records could not be loaded.");
  });
});

describe("CampaignExplorer", () => {
  it("makes each hypothesis the unit: claim, prediction, knowledge, evidence, validation, limits", () => {
    render(<CampaignExplorer detail={CAMPAIGN_DETAIL} />);
    const card = document.getElementById("H1")!;
    const h1 = within(card);
    expect(h1.getByRole("heading", { name: "Price ↓ + Open Interest ↓ · 1h" })).toBeInTheDocument();
    expect(h1.getByText("Long ↗")).toBeInTheDocument();
    expect(h1.getByText("Positive incremental spread versus the price-only Long baseline.")).toBeInTheDocument();
    expect(h1.getByText("Directional prediction")).toBeInTheDocument();
    expect(h1.getByText("Insufficient evidence")).toBeInTheDocument();
    expect(h1.getByText("Not validated — no validation run exists")).toBeInTheDocument();
    expect(h1.getByText("development: 1 qualifying days in segment (< 10)")).toBeInTheDocument();
    expect(h1.getByText(/1 earlier repeat run\(s\) on the same data — not independent evidence/)).toBeInTheDocument();
    expect(h1.getByText(/revision 1 · eeeeeeeeeeee…/)).toBeInTheDocument();
  });

  it("summarises what is known and keeps the recorded conclusion and knowledge history", () => {
    render(<CampaignExplorer detail={CAMPAIGN_DETAIL} />);
    expect(screen.getByText(/All 1 hypotheses: insufficient evidence/)).toBeInTheDocument();
    expect(screen.getByText(/No independent validation has been run/)).toBeInTheDocument();
    expect(screen.getAllByText("We learned nothing about the hypotheses yet.").length).toBeGreaterThan(0);
    expect(screen.getByText(/first recorded as/)).toBeInTheDocument();
    expect(screen.getByText(/Earlier states\s+are not reconstructed/)).toBeInTheDocument();
  });

  it("warns when the ledger is inconsistent with, or behind, the run records", () => {
    render(
      <CampaignExplorer
        detail={{
          ...CAMPAIGN_DETAIL,
          knowledge_check: { problems: ["H1: cited run x does not exist"], uncited_runs: ["H1: a/b"] },
        }}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/do not rely on it: H1: cited run x does not exist/);
    expect(screen.getByRole("alert")).toHaveTextContent(/Newer runs exist/);
  });

  it("says so when no ledger, report or run exists", () => {
    render(
      <CampaignExplorer
        detail={{
          ...CAMPAIGN_DETAIL,
          knowledge_revisions: [],
          knowledge_check: null,
          report: null,
          comparison_runs: {},
          experiment_runs: {},
        }}
      />,
    );
    expect(screen.getByText(/Knowledge has not been derived for this campaign yet/)).toBeInTheDocument();
    expect(screen.getAllByText("Not yet in the ledger").length).toBeGreaterThan(0);
    expect(screen.getByText("No campaign report has been recorded.")).toBeInTheDocument();
    expect(screen.getByText("No runs recorded for this campaign yet.")).toBeInTheDocument();
    expect(screen.getByText("No knowledge ledger revision exists yet.")).toBeInTheDocument();
  });

  it("never upgrades an unsigned prediction's wording", () => {
    const unsigned = {
      ...LEDGER,
      hypotheses: [{ ...LEDGER.hypotheses[0], prediction_sign: "none" as const }],
    };
    render(<CampaignExplorer detail={{ ...CAMPAIGN_DETAIL, knowledge_revisions: [unsigned] }} />);
    expect(screen.getByText("No direction registered — only a difference can be observed")).toBeInTheDocument();
  });
});

describe("ExperimentInspector", () => {
  it("shows the hypothesis it tests and its knowledge before execution detail", () => {
    render(<ExperimentInspector detail={EXPERIMENT_DETAIL} />);
    expect(screen.getByText("Price ↓ + Open Interest ↓ · 1h")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: CAND })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "H1" })).toHaveAttribute("href", `/oink-corp/campaigns/${CAMPAIGN}#H1`);
    expect(screen.getAllByText("Insufficient evidence").length).toBeGreaterThan(0);
    expect(screen.getByText(/Not validated: no validation run exists/)).toBeInTheDocument();
    expect(screen.getByText("Run history (1)")).toBeInTheDocument();
  });

  it("states plainly when an experiment was never run", () => {
    render(<ExperimentInspector detail={{ ...EXPERIMENT_DETAIL, runs: [], comparisons: [], knowledge: [] }} />);
    expect(screen.getByText("Registered but never run — no evidence exists.")).toBeInTheDocument();
  });
});

describe("ResearchHistory", () => {
  it("keeps every immutable run accessible for auditing", () => {
    render(<ResearchHistory history={HISTORY} />);
    expect(screen.getByText("Immutable runs (1)")).toBeInTheDocument();
    expect(screen.getByText(COMPARISON_RUN.run_id)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "cand-vs-base-v1" })).toHaveAttribute(
      "href",
      `/oink-corp/experiments/${CAND}`,
    );
  });

  it("has an empty state", () => {
    render(<ResearchHistory history={{ events: [], runs: [], issues: [] }} />);
    expect(screen.getByText("No research activity recorded yet.")).toBeInTheDocument();
    expect(screen.getByText("No experiment has been run yet.")).toBeInTheDocument();
  });
});

describe("independent testing", () => {
  it("says plainly that a registered plan is not yet a test", () => {
    render(<CampaignExplorer detail={CAMPAIGN_DETAIL} />);
    const card = within(document.getElementById("H1")!);
    expect(card.getByText(/Independently tested on genuinely unseen data/)).toBeInTheDocument();
    expect(
      card.getByText(/Not yet — prospective validation registered for 2026-10-26 to 2026-11-25; awaiting data \(eligible from 2026-11-27\)/),
    ).toBeInTheDocument();
    expect(screen.getByText("Protected holdout")).toBeInTheDocument();
    expect(screen.getByText("Not yet accessed")).toBeInTheDocument();
    expect(screen.getAllByText("Awaiting data")).toHaveLength(2);
  });

  it("answers no when nothing is registered, and warns about exposure", () => {
    const exposed = {
      ...PENDING_EVALUATION,
      partitions: PENDING_EVALUATION.partitions.map((p) =>
        p.role === "holdout" ? { ...p, state: "exposure_uncertain" as const, exposed: true, attempts: 1 } : p,
      ),
      events: [{ seq: 1, at: "2026-12-28T00:00:00+00:00", type: "started", role: "holdout", attempt_id: "a" }],
    };
    const { unmount } = render(<CampaignExplorer detail={{ ...CAMPAIGN_DETAIL, evaluations: [exposed] }} />);
    expect(screen.getByText("Holdout exposed — it can never be re-run")).toBeInTheDocument();
    expect(screen.getByText("Interrupted — exposure uncertain")).toBeInTheDocument();
    expect(screen.getByText("Exposure history (1 events)")).toBeInTheDocument();
    unmount();
    render(<CampaignExplorer detail={{ ...CAMPAIGN_DETAIL, evaluations: [] }} />);
    expect(screen.getByText(/No — no prospective evaluation is registered/)).toBeInTheDocument();
    expect(screen.getByText(/No prospective evaluation is registered: every result/)).toBeInTheDocument();
  });

  it("counts only independent prospective evidence as tested", () => {
    const tested = {
      ...LEDGER,
      hypotheses: [
        {
          ...LEDGER.hypotheses[0],
          evidence: [
            ...LEDGER.hypotheses[0].evidence,
            { ...LEDGER.hypotheses[0].evidence[0], run_id: "p1", evidence_class: "reused" as const, registration_id: "r" },
          ],
        },
      ],
    };
    render(<CampaignExplorer detail={{ ...CAMPAIGN_DETAIL, knowledge_revisions: [tested] }} />);
    expect(screen.queryByText(/^Yes —/)).not.toBeInTheDocument();
    expect(screen.getByText(/Reused data — not independent/)).toBeInTheDocument();
  });

  it("shows the plan on the overview without implying validation", () => {
    render(<ResearchOverview overview={OVERVIEW} />);
    expect(
      screen.getByText(/prospective validation registered \(2026-10-26 → 2026-11-25\), awaiting data until 2026-11-27/),
    ).toBeInTheDocument();
    expect(screen.getByText("Independently validated").nextSibling).toHaveTextContent("0");
  });
});
