"""Research Explorer responses. Specs, campaigns and run records are passed
through as recorded (`dict`) — re-modelling them here would be a second,
drifting definition of artifacts owned by `app.research`."""

from typing import Any, Literal

from pydantic import BaseModel


class ResearchCounts(BaseModel):
    campaigns: int
    hypotheses: int
    experiments_registered: int
    #: Registered experiments with at least one run.
    experiments_executed: int
    comparisons_registered: int
    comparisons_executed: int
    runs: int
    runs_failed: int
    #: segment -> verdict -> count, over the latest run of each experiment
    #: or comparison (never one per repeated run).
    latest_verdicts: dict[str, dict[str, int]]


class CampaignSummary(BaseModel):
    campaign_id: str
    created: str | None
    question: str
    hypotheses: int
    experiments: int
    comparisons: int
    comparisons_executed: int
    comparisons_validated: int
    #: Latest development verdict of each primary comparison, counted.
    development_verdicts: dict[str, int]
    latest_activity: str | None


class RecentRun(BaseModel):
    experiment_id: str
    run_id: str
    kind: Literal["experiment", "incremental_comparison"]
    campaign_id: str | None
    direction: str | None
    segment: str | None
    status: str
    verdict: str | None
    executed_at: str | None
    #: The experiment page that explains this run (a comparison's candidate).
    link_experiment_id: str | None


class ResearchOverview(BaseModel):
    counts: ResearchCounts
    campaigns: list[CampaignSummary]
    recent_runs: list[RecentRun]
    issues: list[dict[str, str]]


class CampaignDetail(BaseModel):
    campaign: dict[str, Any]
    specs: dict[str, dict[str, Any] | None]
    #: experiment_id -> its run records, newest first
    experiment_runs: dict[str, list[dict[str, Any]]]
    #: comparison_id -> its run records, newest first
    comparison_runs: dict[str, list[dict[str, Any]]]
    #: Distinct campaign hashes recorded by its comparison runs.
    campaign_hashes: list[str]
    #: The report split at its `## ` headings, verbatim; None if absent.
    report: list[dict[str, str]] | None
    issues: list[dict[str, str]]


class ExperimentDetail(BaseModel):
    experiment_id: str
    spec: dict[str, Any] | None
    campaigns: list[dict[str, Any]]
    runs: list[dict[str, Any]]
    comparisons: list[dict[str, Any]]
    issues: list[dict[str, str]]
