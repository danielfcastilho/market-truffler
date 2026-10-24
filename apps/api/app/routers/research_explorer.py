"""OINK CORP Research Explorer: read-only views of campaigns, hypotheses and
their knowledge, experiments, and the immutable run records behind them
(see `app.services.research_artifacts`).

Every verdict comes from a run record and every knowledge status from a
knowledge ledger revision (written and verified by
`app.scripts.knowledge_ledger`); this router only gathers and relates them.
Nothing here runs research, derives knowledge, reads datasets, or writes.
"""

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.core.config import get_settings
from app.core.deps import CurrentUserDep
from app.schemas.research_explorer import (
    CampaignDetail,
    CampaignSummary,
    ExperimentDetail,
    HypothesisSummary,
    KnowledgeCounts,
    RankingResearch,
    ResearchCounts,
    ResearchHistory,
    ResearchOverview,
    RunSummary,
)
from app.services.research_artifacts import (
    VERDICTS,
    InvalidIdentifier,
    ResearchArtifacts,
    check_ledger,
    evaluation_states,
    latest_per_segment,
    verdict_of,
)

router = APIRouter(prefix="/api/research", tags=["research"])

OVERVIEW_EVENTS = 8


def _artifacts() -> ResearchArtifacts:
    settings = get_settings()
    return ResearchArtifacts(
        campaigns_dir=Path(settings.research_campaigns_dir),
        experiments_dir=Path(settings.research_experiments_dir),
        data_dir=Path(settings.research_data_dir),
        knowledge_dir=Path(settings.research_knowledge_dir),
        ranking_dir=Path(settings.research_ranking_dir),
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


def _campaign_of(campaigns: list[dict[str, Any]]) -> dict[str, str]:
    """experiment or comparison id -> the (first) campaign it belongs to."""
    owner: dict[str, str] = {}
    for campaign in campaigns:
        for experiment_id in _campaign_experiments(campaign):
            owner.setdefault(experiment_id, campaign["campaign_id"])
        for c in campaign.get("comparisons", []):
            owner.setdefault(c.get("comparison_id"), campaign["campaign_id"])
    return owner


def _evaluation_summaries(
    artifacts: ResearchArtifacts, campaign_id: str | None = None, *, events: bool = False
) -> list[dict[str, Any]]:
    """Prospective evaluation registrations with each partition's state."""
    now = datetime.now(UTC)
    summaries = []
    for e in artifacts.evaluations():
        registration = e["registration"]
        if campaign_id is not None and registration.get("campaign_id") != campaign_id:
            continue
        summary = {
            "registration_id": registration.get("registration_id"),
            "registration_hash": e["registration_hash"],
            "registered_at": registration.get("registered_at"),
            "campaign_id": registration.get("campaign_id"),
            "supersedes": registration.get("supersedes"),
            "protocol_version": registration.get("protocol_version"),
            "hypotheses": [c.get("hypothesis_id") for c in registration.get("claims", [])],
            "embargo_minutes": (registration.get("plan") or {}).get("embargo_minutes"),
            "timestamp_trust": registration.get("timestamp_trust"),
            "partitions": evaluation_states(registration, e["events"], artifacts.data_dir, now),
        }
        if events:
            summary["events"] = e["events"]
        summaries.append(summary)
    return summaries


def _run_summary(
    record: dict[str, Any], owner: dict[str, str], specs: dict[str, dict[str, Any] | None]
) -> RunSummary:
    run_id = str(record.get("experiment_id"))
    kind = record.get("kind")
    is_comparison = kind in ("incremental_comparison", "prospective_evaluation")
    subject = (record.get("candidate") or {}).get("experiment_id") if is_comparison else run_id
    if kind == "ranking_study":
        subject = None  # explained on the ranking research page, not an experiment page
    spec = specs.get(subject) if subject else None
    return RunSummary(
        experiment_id=run_id,
        run_id=str(record.get("run_id")),
        kind=kind
        if kind in ("incremental_comparison", "prospective_evaluation", "ranking_study")
        else "experiment",
        campaign_id=record.get("campaign_id") or owner.get(run_id),
        direction=(spec or {}).get("hypothesis", {}).get("direction"),
        segment=record.get("segment"),
        status=str(record.get("status")),
        verdict=verdict_of(record),
        executed_at=record.get("executed_at"),
        link_experiment_id=subject,
    )


def _events(
    campaigns: list[dict[str, Any]],
    records: list[dict[str, Any]],
    ledgers: dict[str, list[dict[str, Any]]],
    owner: dict[str, str],
    evaluations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Research activity supported by artifacts, newest first: campaign
    registrations (their registered date), evaluations (runs grouped by
    campaign — or standalone experiment — and UTC day, with repeats of an
    already-run experiment and segment counted separately), and knowledge
    ledger revisions with the status changes they recorded."""
    events: list[dict[str, Any]] = [
        {
            "type": "campaign_registered",
            "at": str(c.get("created")),
            "campaign_id": c["campaign_id"],
            "title": c.get("question", ""),
        }
        for c in campaigns
    ]
    seen: set[tuple[str, str]] = set()
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for record in sorted(records, key=lambda r: str(r.get("executed_at", ""))):
        experiment_id = str(record.get("experiment_id"))
        campaign_id = record.get("campaign_id") or owner.get(experiment_id)
        day = str(record.get("executed_at", ""))[:10]
        ranking = record.get("kind") == "ranking_study"
        group = groups.setdefault(
            ("ranking" if ranking else campaign_id or f"experiment:{experiment_id}", day),
            {
                "type": "ranking_evaluation" if ranking else "evaluation",
                "campaign_id": campaign_id,
                "experiment_ids": set(),
                "runs": 0,
                "repeat_runs": 0,
                "failed_runs": 0,
                "segments": set(),
                "verdicts": Counter(),
            },
        )
        key = (experiment_id, str(record.get("segment")))
        group["repeat_runs"] += key in seen
        seen.add(key)
        group["at"] = str(record.get("executed_at"))
        group["runs"] += 1
        group["experiment_ids"].add(experiment_id)
        group["segments"].add(str(record.get("segment")))
        verdict = verdict_of(record)
        if verdict:
            group["verdicts"][verdict] += 1
        else:
            group["failed_runs"] += 1
    for group in groups.values():
        group["experiment_ids"] = sorted(group["experiment_ids"])
        group["segments"] = sorted(group["segments"])
        group["verdicts"] = dict(group["verdicts"])
        events.append(group)
    for campaign_id, revisions in ledgers.items():
        previous: dict[str, str] = {}
        for revision in revisions:
            statuses = {
                h.get("hypothesis_id"): h.get("knowledge_status")
                for h in revision.get("hypotheses", [])
            }
            events.append(
                {
                    "type": "knowledge_revision",
                    "at": str(revision.get("derived_at")),
                    "campaign_id": campaign_id,
                    "revision": revision.get("revision"),
                    "changes": [
                        {"hypothesis_id": h, "from": previous.get(h), "to": s}
                        for h, s in statuses.items()
                        if previous.get(h) != s
                    ],
                }
            )
            previous = statuses
    for e in evaluations or []:
        events.append(
            {
                "type": "evaluation_registered",
                "at": str(e.get("registered_at")),
                "campaign_id": e.get("campaign_id"),
                "registration_id": e.get("registration_id"),
                "hypotheses": e.get("hypotheses", []),
                "partitions": [
                    {"role": p["role"], "start": p["start"], "end": p["end"]}
                    for p in e.get("partitions", [])
                ],
            }
        )
    events.sort(key=lambda e: e["at"], reverse=True)
    return events


@router.get("/overview", response_model=ResearchOverview)
async def research_overview(current_user: CurrentUserDep) -> ResearchOverview:
    artifacts = _artifacts()
    campaigns = artifacts.campaigns()
    experiment_ids = artifacts.experiment_ids()
    records = artifacts.runs()
    by_id = _runs_by_id(records)
    specs = {i: artifacts.spec(i) for i in experiment_ids}
    owner = _campaign_of(campaigns)
    ledgers = {c["campaign_id"]: artifacts.ledger_revisions(c["campaign_id"]) for c in campaigns}
    evaluations = _evaluation_summaries(artifacts)

    # One verdict per experiment/comparison and segment — the latest run.
    verdicts: dict[str, Counter[str]] = {"development": Counter(), "validation": Counter()}
    for runs in by_id.values():
        for segment, record in latest_per_segment(runs).items():
            verdict = verdict_of(record)
            if verdict and segment in verdicts:
                verdicts[segment][verdict] += 1

    statuses: Counter[str] = Counter()
    without_ledger = 0
    summaries = []
    for campaign in campaigns:
        latest = ledgers[campaign["campaign_id"]][-1] if ledgers[campaign["campaign_id"]] else None
        entries = {h.get("hypothesis_id"): h for h in (latest or {}).get("hypotheses", [])}
        hypotheses = []
        for h in campaign.get("hypotheses", []):
            entry = entries.get(h.get("id"))
            spec = specs.get(h.get("experiment_id")) or {}
            if entry:
                statuses[str(entry.get("knowledge_status"))] += 1
            else:
                without_ledger += 1
            hypotheses.append(
                HypothesisSummary(
                    hypothesis_id=h.get("id"),
                    experiment_id=h.get("experiment_id"),
                    direction=spec.get("hypothesis", {}).get("direction"),
                    conditions=spec.get("hypothesis", {}).get("conditions", []),
                    knowledge_status=entry.get("knowledge_status") if entry else None,
                    validation_status=entry.get("validation_status") if entry else None,
                )
            )
        involved = sorted(
            (
                r
                for r in records
                if owner.get(str(r.get("experiment_id"))) == campaign["campaign_id"]
            ),
            key=lambda r: str(r.get("executed_at", "")),
            reverse=True,
        )
        summaries.append(
            CampaignSummary(
                campaign_id=campaign["campaign_id"],
                created=campaign.get("created"),
                question=campaign.get("question", ""),
                hypotheses=hypotheses,
                comparisons=len(campaign.get("comparisons", [])),
                comparisons_executed=sum(
                    1 for c in campaign.get("comparisons", []) if by_id.get(c.get("comparison_id"))
                ),
                ledger_revision=latest.get("revision") if latest else None,
                ledger_derived_at=latest.get("derived_at") if latest else None,
                ledger_problems=len(check_ledger(latest, records)["problems"]) if latest else 0,
                evaluations=[e for e in evaluations if e["campaign_id"] == campaign["campaign_id"]],
                latest_activity=str(involved[0]["executed_at"]) if involved else None,
            )
        )

    comparison_ids = [c.get("comparison_id") for c in campaigns for c in c.get("comparisons", [])]
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
        knowledge=KnowledgeCounts(
            by_status=dict(statuses), hypotheses_without_ledger=without_ledger
        ),
        campaigns=summaries,
        activity=_events(campaigns, records, ledgers, owner, evaluations)[:OVERVIEW_EVENTS],
        issues=artifacts.issues,
    )


@router.get("/history", response_model=ResearchHistory)
async def research_history(current_user: CurrentUserDep) -> ResearchHistory:
    artifacts = _artifacts()
    campaigns = artifacts.campaigns()
    records = artifacts.runs()
    owner = _campaign_of(campaigns)
    specs = {i: artifacts.spec(i) for i in artifacts.experiment_ids()}
    ledgers = {c["campaign_id"]: artifacts.ledger_revisions(c["campaign_id"]) for c in campaigns}
    return ResearchHistory(
        events=_events(campaigns, records, ledgers, owner, _evaluation_summaries(artifacts)),
        runs=[_run_summary(r, owner, specs) for r in records],
        issues=artifacts.issues,
    )


@router.get("/ranking", response_model=RankingResearch)
async def research_ranking(current_user: CurrentUserDep) -> RankingResearch:
    """Ranking research: candidate models, the studies comparing them, and
    every immutable run of each study (newest first). Model-level,
    development-only evidence — kept apart from the feature-level ledger."""
    artifacts = _artifacts()
    candidates = artifacts.ranking_specs("candidates")
    studies = artifacts.ranking_specs("studies")
    by_id = _runs_by_id(artifacts.runs())
    return RankingResearch(
        candidates=list(candidates.values()),
        studies=[
            {
                "study": study,
                "candidate": candidates.get(str(study.get("candidate"))),
                "reference": candidates.get(str(study.get("reference")))
                if study.get("reference")
                else None,
                "runs": [r for r in by_id.get(study_id, []) if r.get("kind") == "ranking_study"],
            }
            for study_id, study in studies.items()
        ],
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
        revisions = artifacts.ledger_revisions(campaign_id)
    except InvalidIdentifier as exc:
        raise _not_found("campaign") from exc
    records = artifacts.runs()
    by_id = _runs_by_id(records)
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
        knowledge_revisions=revisions,
        knowledge_check=check_ledger(revisions[-1], records) if revisions else None,
        evaluations=_evaluation_summaries(artifacts, campaign_id, events=True),
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
    knowledge = []
    for campaign in artifacts.campaigns():
        roles = _campaign_experiments(campaign).get(experiment_id)
        if roles:
            campaigns.append({"campaign_id": campaign["campaign_id"], "roles": roles})
        revisions = artifacts.ledger_revisions(campaign["campaign_id"]) if roles else []
        for entry in revisions[-1].get("hypotheses", []) if revisions else []:
            if entry.get("experiment_id") == experiment_id:
                knowledge.append(
                    {
                        "campaign_id": campaign["campaign_id"],
                        "revision": revisions[-1].get("revision"),
                        "hypothesis_id": entry.get("hypothesis_id"),
                        "knowledge_status": entry.get("knowledge_status"),
                        "validation_status": entry.get("validation_status"),
                    }
                )
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
        knowledge=knowledge,
        runs=by_id.get(experiment_id, []),
        comparisons=comparisons,
        issues=artifacts.issues,
    )
