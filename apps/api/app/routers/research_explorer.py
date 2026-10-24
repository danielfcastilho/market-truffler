"""OINK CORP Research Explorer: read-only views of campaigns, experiments
and their immutable run records (see `app.services.research_artifacts`).

Every number and verdict comes from a recorded artifact; this router only
gathers and relates them. Nothing here runs research, reads datasets, or
writes files.
"""

from collections import Counter
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.core.config import get_settings
from app.core.deps import CurrentUserDep
from app.schemas.research_explorer import (
    CampaignDetail,
    CampaignSummary,
    ExperimentDetail,
    RecentRun,
    ResearchCounts,
    ResearchOverview,
)
from app.services.research_artifacts import (
    VERDICTS,
    InvalidIdentifier,
    ResearchArtifacts,
    latest_per_segment,
    verdict_of,
)

router = APIRouter(prefix="/api/research", tags=["research"])

RECENT_RUNS = 15


def _artifacts() -> ResearchArtifacts:
    settings = get_settings()
    return ResearchArtifacts(
        campaigns_dir=Path(settings.research_campaigns_dir),
        experiments_dir=Path(settings.research_experiments_dir),
        data_dir=Path(settings.research_data_dir),
    )


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found")


def _runs_by_id(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:  # newest first, preserved
        grouped.setdefault(str(record.get("experiment_id")), []).append(record)
    return grouped


def _campaign_experiments(campaign: dict[str, Any]) -> dict[str, list[str]]:
    """experiment_id -> the roles it plays in this campaign."""
    roles: dict[str, list[str]] = {}
    for h in campaign.get("hypotheses", []):
        roles.setdefault(h.get("experiment_id"), []).append(f"hypothesis {h.get('id')}")
    for b in campaign.get("baselines", []):
        if b.get("experiment_id"):
            roles.setdefault(b["experiment_id"], []).append(f"baseline {b.get('id')}")
    for c in campaign.get("comparisons", []):
        roles.setdefault(c.get("candidate"), []).append(f"candidate in {c.get('comparison_id')}")
        roles.setdefault(c.get("reference"), []).append(f"reference in {c.get('comparison_id')}")
    return roles


def _latest_activity(records: list[dict[str, Any]]) -> str | None:
    return str(records[0]["executed_at"]) if records else None


@router.get("/overview", response_model=ResearchOverview)
async def research_overview(current_user: CurrentUserDep) -> ResearchOverview:
    artifacts = _artifacts()
    campaigns = artifacts.campaigns()
    experiment_ids = artifacts.experiment_ids()
    records = artifacts.runs()
    by_id = _runs_by_id(records)
    specs = {i: artifacts.spec(i) for i in experiment_ids}

    comparison_ids = {
        c.get("comparison_id"): campaign["campaign_id"]
        for campaign in campaigns
        for c in campaign.get("comparisons", [])
    }
    campaign_of_experiment: dict[str, str] = {}
    for campaign in campaigns:
        for experiment_id in _campaign_experiments(campaign):
            campaign_of_experiment.setdefault(experiment_id, campaign["campaign_id"])

    # One verdict per experiment/comparison and segment — the latest run —
    # so repeated runs never count as more evidence.
    verdicts: dict[str, Counter[str]] = {"development": Counter(), "validation": Counter()}
    for runs in by_id.values():
        for segment, record in latest_per_segment(runs).items():
            verdict = verdict_of(record)
            if verdict and segment in verdicts:
                verdicts[segment][verdict] += 1

    summaries = []
    for campaign in campaigns:
        primary = [by_id.get(c.get("comparison_id"), []) for c in campaign.get("comparisons", [])]
        primary_verdicts: Counter[str] = Counter()
        validated = 0
        for runs in primary:
            latest = latest_per_segment(runs)
            verdict = verdict_of(latest["development"]) if "development" in latest else None
            if verdict:
                primary_verdicts[verdict] += 1
            validated += "validation" in latest
        involved = [
            r
            for i in [
                *_campaign_experiments(campaign),
                *[c.get("comparison_id") for c in campaign.get("comparisons", [])],
            ]
            for r in by_id.get(i, [])
        ]
        involved.sort(key=lambda r: str(r.get("executed_at", "")), reverse=True)
        summaries.append(
            CampaignSummary(
                campaign_id=campaign["campaign_id"],
                created=campaign.get("created"),
                question=campaign.get("question", ""),
                hypotheses=len(campaign.get("hypotheses", [])),
                experiments=len(_campaign_experiments(campaign)),
                comparisons=len(campaign.get("comparisons", [])),
                comparisons_executed=sum(1 for runs in primary if runs),
                comparisons_validated=validated,
                development_verdicts={v: primary_verdicts[v] for v in VERDICTS},
                latest_activity=_latest_activity(involved),
            )
        )

    recent = []
    for record in records[:RECENT_RUNS]:
        run_id = str(record.get("experiment_id"))
        is_comparison = record.get("kind") == "incremental_comparison"
        subject = (record.get("candidate") or {}).get("experiment_id") if is_comparison else run_id
        spec = specs.get(subject) if subject else None
        recent.append(
            RecentRun(
                experiment_id=run_id,
                run_id=str(record.get("run_id")),
                kind="incremental_comparison" if is_comparison else "experiment",
                campaign_id=record.get("campaign_id")
                or comparison_ids.get(run_id)
                or campaign_of_experiment.get(run_id),
                direction=(spec or {}).get("hypothesis", {}).get("direction"),
                segment=record.get("segment"),
                status=str(record.get("status")),
                verdict=verdict_of(record),
                executed_at=record.get("executed_at"),
                link_experiment_id=subject,
            )
        )

    executed = {
        str(r.get("experiment_id")) for r in records if r.get("kind") != "incremental_comparison"
    }
    return ResearchOverview(
        counts=ResearchCounts(
            campaigns=len(campaigns),
            hypotheses=sum(len(c.get("hypotheses", [])) for c in campaigns),
            experiments_registered=len(experiment_ids),
            experiments_executed=len(executed & set(experiment_ids)),
            comparisons_registered=len(comparison_ids),
            comparisons_executed=sum(1 for i in comparison_ids if by_id.get(i)),
            runs=len(records),
            runs_failed=sum(1 for r in records if r.get("status") != "completed"),
            latest_verdicts={s: {v: c[v] for v in VERDICTS} for s, c in verdicts.items()},
        ),
        campaigns=summaries,
        recent_runs=recent,
        issues=artifacts.issues,
    )


@router.get("/campaigns/{campaign_id}", response_model=CampaignDetail)
async def research_campaign(campaign_id: str, current_user: CurrentUserDep) -> CampaignDetail:
    artifacts = _artifacts()
    try:
        campaign = artifacts.campaign(campaign_id)
        if campaign is None:
            raise _not_found("campaign")
        roles = _campaign_experiments(campaign)
        specs = {i: artifacts.spec(i) for i in roles if i}
        report = artifacts.report_sections(campaign_id)
    except InvalidIdentifier as exc:
        raise _not_found("campaign") from exc
    by_id = _runs_by_id(artifacts.runs())
    comparison_runs = {
        c["comparison_id"]: by_id.get(c["comparison_id"], [])
        for c in campaign.get("comparisons", [])
    }
    hashes = sorted(
        {
            str(r["campaign_hash"])
            for runs in comparison_runs.values()
            for r in runs
            if r.get("campaign_hash")
        }
    )
    return CampaignDetail(
        campaign=campaign,
        specs=specs,
        experiment_runs={i: by_id.get(i, []) for i in roles if i},
        comparison_runs=comparison_runs,
        campaign_hashes=hashes,
        report=report,
        issues=artifacts.issues,
    )


@router.get("/experiments/{experiment_id}", response_model=ExperimentDetail)
async def research_experiment(experiment_id: str, current_user: CurrentUserDep) -> ExperimentDetail:
    artifacts = _artifacts()
    try:
        spec = artifacts.spec(experiment_id)
    except InvalidIdentifier as exc:
        raise _not_found("experiment") from exc
    records = artifacts.runs()
    by_id = _runs_by_id(records)
    if spec is None and experiment_id not in by_id:
        raise _not_found("experiment")

    campaigns = []
    comparisons = []
    for campaign in artifacts.campaigns():
        roles = _campaign_experiments(campaign).get(experiment_id)
        if roles:
            campaigns.append({"campaign_id": campaign["campaign_id"], "roles": roles})
        for c in campaign.get("comparisons", []):
            if experiment_id in (c.get("candidate"), c.get("reference")):
                comparisons.append(
                    {
                        "campaign_id": campaign["campaign_id"],
                        "comparison": c,
                        "role": "candidate" if c.get("candidate") == experiment_id else "reference",
                        "runs": by_id.get(c.get("comparison_id"), []),
                    }
                )
    return ExperimentDetail(
        experiment_id=experiment_id,
        spec=spec,
        campaigns=campaigns,
        runs=by_id.get(experiment_id, []),
        comparisons=comparisons,
        issues=artifacts.issues,
    )
