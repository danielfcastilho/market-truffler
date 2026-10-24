"""OINK CORP: run a pre-registered research campaign (see
`app.research.campaign`) — every experiment through the existing runner
and Evaluator, then every incremental comparison — and record each as an
immutable run under `<data-dir>/runs/`.

    python -m app.scripts.run_campaign \\
        --campaign research/oink_corp/campaigns/<campaign_id>.json \\
        --segment development [--data-dir research/oink_corp/data]

Parquet only, like `run_experiment`. Validate without running anything
with `--check`.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from app.research.campaign import Campaign, Comparison, campaign_hash, load_campaign
from app.research.comparison import (
    COMPARISON_VERSION,
    comparison_seed,
    evaluate_incremental,
)
from app.research.dataset import code_fingerprint, get_git_commit
from app.research.evaluation import EVALUATOR_VERSION, Segment, plan_split
from app.research.experiment import (
    ExperimentSpec,
    ExperimentSpecError,
    _canonical_hash,
    hypothesis_hash,
    load_datasets,
    select,
    spec_hash,
)
from app.research.runs import (
    RUNS_DIRNAME,
    check_experiment_identity,
    load_runs,
    new_run_id,
    validation_reuse,
    write_run,
)
from app.scripts import run_experiment


def _comparison_definition(
    campaign: Campaign, comparison: Comparison, specs: dict[str, ExperimentSpec]
) -> dict[str, Any]:
    return {
        "comparison_version": COMPARISON_VERSION,
        "campaign_id": campaign.campaign_id,
        "comparison_id": comparison.comparison_id,
        "hypothesis": comparison.hypothesis,
        "candidate": specs[comparison.candidate].model_dump(mode="json"),
        "reference": specs[comparison.reference].model_dump(mode="json"),
    }


def run_comparison(
    campaign: Campaign,
    c_hash: str,
    comparison: Comparison,
    specs: dict[str, ExperimentSpec],
    segment: Segment,
    data_dir: Path,
) -> tuple[Path, dict[str, Any]]:
    candidate, reference = specs[comparison.candidate], specs[comparison.reference]
    definition = _comparison_definition(campaign, comparison, specs)
    definition_hash = _canonical_hash(definition)
    runs_root = data_dir / RUNS_DIRNAME
    history = load_runs(runs_root)
    check_experiment_identity(history, comparison.comparison_id, definition_hash)

    executed_at = datetime.now(UTC)
    seed = comparison_seed(spec_hash(candidate), spec_hash(reference), segment)
    record: dict[str, Any] = {
        "run_id": new_run_id(executed_at),
        "executed_at": executed_at.isoformat(),
        "kind": "incremental_comparison",
        # `experiment_id`/`spec_hash`/`hypothesis_hash` keep comparison runs
        # in the same identity and validation-reuse bookkeeping as experiments.
        "experiment_id": comparison.comparison_id,
        "spec_hash": definition_hash,
        "hypothesis_hash": hypothesis_hash(candidate),
        "campaign_id": campaign.campaign_id,
        "campaign_hash": c_hash,
        "hypothesis": comparison.hypothesis,
        "candidate": {
            "experiment_id": candidate.experiment_id,
            "spec_hash": spec_hash(candidate),
            "hypothesis_hash": hypothesis_hash(candidate),
        },
        "reference": {
            "experiment_id": reference.experiment_id,
            "spec_hash": spec_hash(reference),
            "hypothesis_hash": hypothesis_hash(reference),
        },
        "label": candidate.label,
        "segment": segment,
        "comparison_version": COMPARISON_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "code_fingerprint": code_fingerprint(),
        "git_commit": get_git_commit(),
        "seed": seed,
    }
    try:
        # The candidate's columns are a superset of the reference's.
        loaded = load_datasets(candidate, data_dir)
        record["datasets"] = [
            {"name": ref.name, "data_sha256": manifest["data_sha256"]}
            for ref, manifest in zip(candidate.datasets, loaded.manifests, strict=True)
        ]
        candidate_obs = select(candidate, loaded.table)
        reference_obs = select(reference, loaded.table)
        split = plan_split(candidate_obs.frame_time)
        record["report"] = evaluate_incremental(
            candidate_obs,
            reference_obs,
            segment=segment,
            split=split,
            horizon_minutes=loaded.horizon_minutes,
            seed=seed,
        )
        record["report"]["validation_reuse"] = validation_reuse(
            history,
            validation_days=[d.isoformat() for d in split.validation_days],
            hypothesis_hash=record["hypothesis_hash"],
            label=candidate.label,
        )
        record["status"] = "completed"
        record["error"] = None
    except (ExperimentSpecError, OSError, ValueError, KeyError) as exc:
        record["status"] = "failed"
        record["error"] = f"{type(exc).__name__}: {exc}"
    return write_run(runs_root, record, definition), record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--campaign", required=True, type=Path)
    parser.add_argument("--segment", choices=["development", "validation"])
    parser.add_argument("--check", action="store_true", help="validate only; run nothing")
    parser.add_argument(
        "--experiments-dir",
        type=Path,
        help="where the campaign's specs live (default: ../experiments next to the campaign)",
    )
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    experiments_dir = args.experiments_dir or args.campaign.resolve().parent.parent / "experiments"
    try:
        campaign, specs = load_campaign(args.campaign, experiments_dir)
    except ExperimentSpecError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        sys.exit(1)
    c_hash = campaign_hash(campaign, specs)
    print(f"campaign {campaign.campaign_id}  hash {c_hash}")
    if args.check:
        print(f"valid: {len(specs)} experiments, {len(campaign.comparisons)} comparisons")
        return
    if args.segment is None:
        parser.error("--segment is required unless --check")
    if args.data_dir is None:
        from app.core.config import get_settings

        args.data_dir = Path(get_settings().research_data_dir)
    segment = cast(Segment, args.segment)

    failed = False
    for experiment_id in campaign.experiment_ids:
        try:
            run_dir, record = run_experiment.run(
                experiments_dir / f"{experiment_id}.json", segment, args.data_dir
            )
        except ExperimentSpecError as exc:
            print(f"refused {experiment_id}: {exc}", file=sys.stderr)
            sys.exit(1)
        failed |= record["status"] != "completed"
        print(_summary(experiment_id, record, run_dir))
    for comparison in campaign.comparisons:
        try:
            run_dir, record = run_comparison(
                campaign, c_hash, comparison, specs, segment, args.data_dir
            )
        except ExperimentSpecError as exc:
            print(f"refused {comparison.comparison_id}: {exc}", file=sys.stderr)
            sys.exit(1)
        failed |= record["status"] != "completed"
        print(_summary(comparison.comparison_id, record, run_dir))
    print("Evidence about one sample only — not proof of a profitable strategy.")
    sys.exit(1 if failed else 0)


def _summary(name: str, record: dict[str, Any], run_dir: Path) -> str:
    if record["status"] != "completed":
        return f"{name}: FAILED {record['error']}  ({run_dir})"
    report = record["report"]
    reasons = "; ".join(report["verdict_reasons"])
    spread = (report.get("spread") or {}).get("mean")
    return json.dumps(
        {"run": name, "verdict": report["verdict"], "mean_spread": spread, "why": reasons}
    )


if __name__ == "__main__":
    main()
