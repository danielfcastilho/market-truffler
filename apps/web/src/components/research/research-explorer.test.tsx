import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CampaignExplorer } from "./campaign-explorer";
import { ExperimentInspector } from "./experiment-inspector";
import { ResearchOverview } from "./research-overview";
import { ResearchUnavailable } from "./research-unavailable";
import { RunEvidence } from "./run-evidence";
import {
  BASE,
  CAMPAIGN,
  CAMPAIGN_DETAIL,
  CAND,
  COMPARISON_RUN,
  EXPERIMENT_DETAIL,
  INSUFFICIENT_RUN,
  OVERVIEW,
  POSITIVE_RUN,
} from "./test-fixtures";
import { VerdictBadge } from "./verdict-badge";

describe("VerdictBadge", () => {
  it("uses exactly the backend verdicts, and marks failed runs", () => {
    const { rerender } = render(<VerdictBadge verdict="insufficient_data" />);
    expect(screen.getByText("Insufficient data")).toHaveAttribute("data-verdict", "insufficient_data");
    rerender(<VerdictBadge verdict={null} failed />);
    expect(screen.getByText("Run failed")).toBeInTheDocument();
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
    expect(screen.getByText("−0.145 pp")).toBeInTheDocument();
  });

  it("shows the recorded interval for an evidence verdict, without the observation caveat", () => {
    render(<RunEvidence record={POSITIVE_RUN} />);
    expect(screen.getByText("Positive evidence")).toBeInTheDocument();
    expect(screen.getByText(/90% interval of the spread/)).toHaveTextContent("+0.040 pp");
    expect(screen.queryByText("Descriptive observations — not evidence")).not.toBeInTheDocument();
  });

  it("shows a failed run's error instead of any figures", () => {
    render(
      <RunEvidence record={{ ...INSUFFICIENT_RUN, status: "failed", error: "OSError: gone" }} />,
    );
    expect(screen.getByText(/No evaluation was produced: OSError: gone/)).toBeInTheDocument();
    expect(screen.queryByText("Timestamps with selections")).not.toBeInTheDocument();
  });

  it("names comparison figures against the reference group", () => {
    render(<RunEvidence record={COMPARISON_RUN} />);
    expect(screen.getByText("Reference group mean")).toBeInTheDocument();
    expect(screen.getByText("Incremental spread (timestamp-weighted)")).toBeInTheDocument();
  });
});

describe("ResearchOverview", () => {
  it("shows real counts, campaigns and recent runs with links", () => {
    render(<ResearchOverview overview={OVERVIEW} />);
    expect(screen.getByText("7 / 7")).toBeInTheDocument();
    expect(screen.getByText(/Activity is not success/)).toBeInTheDocument();
    const campaign = screen.getByRole("link", { name: /Does Open Interest add information/ });
    expect(campaign).toHaveAttribute("href", `/oink-corp/campaigns/${CAMPAIGN}`);
    expect(within(campaign).getByText(/no validation run/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "cand-vs-base-v1" })).toHaveAttribute(
      "href",
      `/oink-corp/experiments/${CAND}`,
    );
  });

  it("has an honest empty state", () => {
    render(
      <ResearchOverview
        overview={{
          ...OVERVIEW,
          counts: { ...OVERVIEW.counts, campaigns: 0, runs: 0, experiments_registered: 0 },
          campaigns: [],
          recent_runs: [],
        }}
      />,
    );
    expect(screen.getByText("No research has been registered yet.")).toBeInTheDocument();
  });

  it("surfaces unreadable artifacts instead of hiding them", () => {
    render(
      <ResearchOverview
        overview={{ ...OVERVIEW, issues: [{ artifact: "broken-v1.json", problem: "unreadable" }] }}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("broken-v1.json: unreadable");
  });

  it("reports an unreachable API as an error, never as empty research", () => {
    render(<ResearchUnavailable message="research API unreachable" />);
    expect(screen.getByRole("alert")).toHaveTextContent("Research records could not be loaded.");
  });
});

describe("CampaignExplorer", () => {
  it("explains the question, hypotheses, evidence and recorded conclusion", () => {
    render(<CampaignExplorer detail={CAMPAIGN_DETAIL} />);
    expect(screen.getByRole("heading", { name: /Does Open Interest add information/ })).toBeInTheDocument();
    expect(screen.getByText(/All 1 executed primary comparison: insufficient data/)).toBeInTheDocument();
    expect(screen.getByText(/No independent validation has been run/)).toBeInTheDocument();
    expect(screen.getByText("Positive incremental spread versus the price-only Long baseline.")).toBeInTheDocument();
    expect(screen.getAllByText(/1-hour Open Interest change is negative/).length).toBeGreaterThan(0);
    expect(screen.getAllByText("· not validated").length).toBe(1);
    // Once under "Recorded conclusion", once in the collapsed full report.
    expect(screen.getAllByText("We learned nothing about the hypotheses yet.")).toHaveLength(2);
    expect(screen.getAllByText("tested — insufficient data")).toHaveLength(2);
    expect(screen.getAllByText("c".repeat(64)).length).toBe(1);
    expect(screen.getAllByText("not computed").length).toBeGreaterThan(0);
  });

  it("says so when no report or run exists", () => {
    render(
      <CampaignExplorer
        detail={{ ...CAMPAIGN_DETAIL, report: null, comparison_runs: {}, experiment_runs: {} }}
      />,
    );
    expect(screen.getByText("No campaign report has been recorded.")).toBeInTheDocument();
    expect(screen.getByText(/none of its 1 primary comparisons has been run/)).toBeInTheDocument();
    expect(screen.getByText("No runs recorded for this campaign yet.")).toBeInTheDocument();
  });
});

describe("ExperimentInspector", () => {
  it("explains the definition, evaluation, comparisons and every run", () => {
    render(<ExperimentInspector detail={EXPERIMENT_DETAIL} />);
    expect(screen.getByRole("heading", { name: BASE })).toBeInTheDocument();
    expect(screen.getAllByText(/1-hour return is in the bottom 20%/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Not validated: no validation run exists/)).toBeInTheDocument();
    expect(screen.getByText(/this experiment is the reference/)).toBeInTheDocument();
    expect(screen.getByText(/A difference is not\s+automatically proof/)).toBeInTheDocument();
    expect(screen.getByText("Run history (1)")).toBeInTheDocument();
    expect(screen.getByText(INSUFFICIENT_RUN.run_id)).toBeInTheDocument();
  });

  it("states plainly when an experiment was never run", () => {
    render(<ExperimentInspector detail={{ ...EXPERIMENT_DETAIL, runs: [], comparisons: [] }} />);
    expect(screen.getByText("Registered but never run — no evidence exists.")).toBeInTheDocument();
  });
});
