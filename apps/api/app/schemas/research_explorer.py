"""Research Explorer responses. Specs, campaigns, run records and knowledge
ledger revisions are passed through as recorded (`dict`) — re-modelling
them here would be a second, drifting definition of artifacts owned by
`app.research`."""

from datetime import datetime
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


class KnowledgeCounts(BaseModel):
    #: knowledge_status -> hypotheses, from each campaign's latest ledger revision.
    by_status: dict[str, int]
    #: Registered hypotheses whose campaign has no ledger revision yet.
    hypotheses_without_ledger: int


class HypothesisSummary(BaseModel):
    hypothesis_id: str
    experiment_id: str
    direction: str | None
    #: The experiment's registered selection conditions (for a readable title).
    conditions: list[dict[str, Any]]
    #: From the latest ledger revision; None when there is none.
    knowledge_status: str | None
    validation_status: str | None


class CampaignSummary(BaseModel):
    campaign_id: str
    created: str | None
    question: str
    hypotheses: list[HypothesisSummary]
    comparisons: int
    comparisons_executed: int
    ledger_revision: int | None
    ledger_derived_at: str | None
    #: Cited runs that don't match their records (see `check_ledger`).
    ledger_problems: int
    #: Prospective evaluation registrations and their partition states.
    evaluations: list[dict[str, Any]]
    latest_activity: str | None


class RunSummary(BaseModel):
    experiment_id: str
    run_id: str
    kind: Literal["experiment", "incremental_comparison", "prospective_evaluation", "ranking_study"]
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
    knowledge: KnowledgeCounts
    campaigns: list[CampaignSummary]
    #: The latest research events (see `ResearchHistory.events`).
    activity: list[dict[str, Any]]
    issues: list[dict[str, str]]


class RankingResearch(BaseModel):
    """Ranking candidates and studies as committed, with each study's runs."""

    candidates: list[dict[str, Any]]
    #: {study, candidate, reference, runs} per registered study
    studies: list[dict[str, Any]]
    issues: list[dict[str, str]]


class DataHealth(BaseModel):
    """Research data health (`app.services.research_data_health`):
    descriptive coverage, shard and readiness facts — no verdicts."""

    computed_at: datetime
    cache_seconds: float
    frames: dict[str, Any]
    shards: dict[str, Any]
    shard_continuity: dict[str, Any]
    temporal_contract: dict[str, Any]
    readiness: dict[str, Any]
    warnings: list[str]
    limitations: list[str]


class ModelKnowledge(BaseModel):
    """Model knowledge ledgers (latest revision of each ranking model and
    qualification policy) and the Truffle registrations that bind them."""

    #: {kind, id, revision, derived_at, status, status_basis, identity,
    #: evidence_counts, evidence, links, limitations, chain_problems}
    models: list[dict[str, Any]]
    #: Truffle registrations with each partition's lifecycle state and any
    #: completed decisions.
    truffle_registrations: list[dict[str, Any]]
    issues: list[dict[str, str]]


class ResearchHistory(BaseModel):
    #: campaign_registered | evaluation_registered | evaluation |
    #: knowledge_revision, newest first.
    events: list[dict[str, Any]]
    #: Every immutable run, newest first.
    runs: list[RunSummary]
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
    #: Knowledge ledger revisions, oldest first (empty: no ledger yet).
    knowledge_revisions: list[dict[str, Any]]
    #: The latest revision checked against the run records it cites.
    knowledge_check: dict[str, list[str]] | None
    #: Prospective evaluation registrations, partition states, exposure events.
    evaluations: list[dict[str, Any]]
    issues: list[dict[str, str]]


class ExperimentDetail(BaseModel):
    experiment_id: str
    spec: dict[str, Any] | None
    campaigns: list[dict[str, Any]]
    #: Hypotheses this experiment tests, with their latest knowledge status.
    knowledge: list[dict[str, Any]]
    runs: list[dict[str, Any]]
    comparisons: list[dict[str, Any]]
    issues: list[dict[str, str]]
