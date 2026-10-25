"""OINK CORP: run one experiment spec against its materialized research
datasets and record the result (see `app.research.experiment`,
`app.research.evaluation`, `app.research.runs`).

Reads Parquet only — no database, no replay — so it runs inside the api
container or on a host checkout alike:

    python -m app.scripts.run_experiment \\
        --spec research/oink_corp/experiments/<experiment_id>.json \\
        --segment development [--data-dir research/oink_corp/data]

`--segment validation` evaluates the chronological validation days (and is
counted as validation reuse). There is deliberately no holdout access.
Every run — completed or failed — is written as an immutable directory
under `<data-dir>/runs/`.
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from app.research.dataset import code_fingerprint, get_git_commit
from app.research.evaluation import (
    EVALUATOR_VERSION,
    Segment,
    bootstrap_seed,
    evaluate,
    plan_split,
)
from app.research.experiment import (
    RECONSTRUCTED_DISCLOSURE,
    SPEC_VERSION,
    ExperimentSpecError,
    hypothesis_hash,
    load_datasets,
    load_spec,
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--segment", required=True, choices=["development", "validation"])
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="directory holding the datasets and runs/ (default: Settings.research_data_dir)",
    )
    return parser


def run(spec_path: Path, segment: Segment, data_dir: Path) -> tuple[Path, dict[str, Any]]:
    spec = load_spec(spec_path)
    s_hash = spec_hash(spec)
    h_hash = hypothesis_hash(spec)
    runs_root = data_dir / RUNS_DIRNAME
    history = load_runs(runs_root)
    check_experiment_identity(history, spec.experiment_id, s_hash)

    executed_at = datetime.now(UTC)
    seed = bootstrap_seed(s_hash, segment)
    record: dict[str, Any] = {
        "run_id": new_run_id(executed_at),
        "executed_at": executed_at.isoformat(),
        "experiment_id": spec.experiment_id,
        "heuristic_id": spec.heuristic.id,
        "spec_version": SPEC_VERSION,
        "spec_hash": s_hash,
        "hypothesis_hash": h_hash,
        "label": spec.label,
        "segment": segment,
        "evaluator_version": EVALUATOR_VERSION,
        "code_fingerprint": code_fingerprint(),
        "git_commit": get_git_commit(),
        "seed": seed,
        "incomplete_run_dirs_ignored": [str(p) for p in history.incomplete_dirs],
    }
    try:
        loaded = load_datasets(spec, data_dir)
        record["datasets"] = [
            {
                "name": ref.name,
                "data_sha256": manifest["data_sha256"],
                "dataset_schema_version": manifest["dataset_schema_version"],
                "sample_interval_minutes": manifest["sample_interval_minutes"],
                "first_frame_time": manifest["first_frame_time"],
                "last_frame_time": manifest["last_frame_time"],
                "code_fingerprint": manifest.get("code_fingerprint"),
            }
            for ref, manifest in zip(spec.datasets, loaded.manifests, strict=True)
        ]
        record["observation_source"] = loaded.observation_source
        observations = select(spec, loaded.table)
        split = plan_split(observations.frame_time)
        reuse = validation_reuse(
            history,
            validation_days=[d.isoformat() for d in split.validation_days],
            hypothesis_hash=h_hash,
            label=spec.label,
        )
        record["report"] = evaluate(
            observations,
            segment=segment,
            split=split,
            horizon_minutes=loaded.horizon_minutes,
            sample_interval_minutes=loaded.sample_interval_minutes,
            seed=seed,
            validation_reuse=reuse,
        )
        record["report"]["timestamps_percentile_universe_too_small"] = (
            observations.timestamps_percentile_universe_too_small
        )
        if loaded.observation_source == "reconstructed":
            record["report"].setdefault("disclosures", []).append(RECONSTRUCTED_DISCLOSURE)
        record["status"] = "completed"
        record["error"] = None
    except (ExperimentSpecError, OSError, ValueError, KeyError) as exc:
        record["status"] = "failed"
        record["error"] = f"{type(exc).__name__}: {exc}"

    run_dir = write_run(runs_root, record, spec.model_dump(mode="json"))
    return run_dir, record


def main() -> None:
    args = _build_parser().parse_args()
    if args.data_dir is None:
        from app.core.config import get_settings

        args.data_dir = Path(get_settings().research_data_dir)
    try:
        run_dir, record = run(args.spec, cast(Segment, args.segment), args.data_dir)
    except ExperimentSpecError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"run recorded: {run_dir}")
    if record["status"] != "completed":
        print(f"FAILED: {record['error']}", file=sys.stderr)
        sys.exit(1)
    report = record["report"]
    print(f"segment {report['segment']}: verdict {report['verdict']}")
    for reason in report["verdict_reasons"]:
        print(f"  - {reason}")
    if "spread" in report:
        print(f"mean spread: {report['spread']['mean']}")
    print("This is evidence about one sample, not proof of a profitable strategy.")


if __name__ == "__main__":
    main()
