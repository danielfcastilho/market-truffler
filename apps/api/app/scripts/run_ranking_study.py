"""OINK CORP: run one ranking study (see `app.research.ranking`) on its
development data and record the result as an immutable run under
`<data-dir>/runs/<study_id>/`.

    python -m app.scripts.run_ranking_study \\
        --study research/oink_corp/ranking/studies/<study_id>.json \\
        [--data-dir research/oink_corp/data]

Parquet only, through the reservation-guarded loader: reserved validation
and protected-holdout periods are refused, never read. Development segment
only. A rerun of an unchanged study is recorded as a repeat of the earlier
runs, never as independent confirmation.
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.research.dataset import code_fingerprint, get_git_commit
from app.research.experiment import ExperimentSpecError
from app.research.ranking import (
    PRIMARY_METRIC,
    RANKING_EVALUATOR_VERSION,
    candidate_hash,
    evaluate_study,
    load_study,
    study_hash,
)
from app.research.runs import (
    RUNS_DIRNAME,
    check_experiment_identity,
    load_runs,
    new_run_id,
    write_run,
)


def run(study_path: Path, data_dir: Path) -> tuple[Path, dict[str, Any]]:
    candidates_dir = study_path.resolve().parent.parent / "candidates"
    study, candidate, reference = load_study(study_path, candidates_dir)
    s_hash = study_hash(study, candidate, reference)
    runs_root = data_dir / RUNS_DIRNAME
    history = load_runs(runs_root)
    check_experiment_identity(history, study.study_id, s_hash)
    executed_at = datetime.now(UTC)
    seed = int(s_hash[:16], 16)
    record: dict[str, Any] = {
        "run_id": new_run_id(executed_at),
        "executed_at": executed_at.isoformat(),
        "kind": "ranking_study",
        "experiment_id": study.study_id,
        "spec_hash": s_hash,
        "segment": "development",
        "label": study.label,
        "direction": candidate.direction,
        "primary_metric": PRIMARY_METRIC,
        "ranking_evaluator_version": RANKING_EVALUATOR_VERSION,
        "candidate": {
            "candidate_id": candidate.candidate_id,
            "spec_hash": candidate_hash(candidate),
        },
        "reference": (
            {"candidate_id": reference.candidate_id, "spec_hash": candidate_hash(reference)}
            if reference
            else None
        ),
        "repeat_of": [
            f"{r['experiment_id']}/{r['run_id']}"
            for r in history.records
            if r.get("experiment_id") == study.study_id and r.get("status") == "completed"
        ],
        "code_fingerprint": code_fingerprint(),
        "git_commit": get_git_commit(),
        "seed": seed,
    }
    try:
        report, manifests = evaluate_study(study, candidate, reference, data_dir, seed=seed)
        record["datasets"] = [
            {
                "name": ref.name,
                "data_sha256": m["data_sha256"],
                "first_frame_time": m.get("first_frame_time"),
                "last_frame_time": m.get("last_frame_time"),
            }
            for ref, m in zip(study.datasets, manifests, strict=True)
        ]
        record["report"] = report
        record["status"] = "completed"
        record["error"] = None
    except (ExperimentSpecError, OSError, ValueError, KeyError) as exc:
        record["status"] = "failed"
        record["error"] = f"{type(exc).__name__}: {exc}"
    spec_json = {
        "study": study.model_dump(mode="json"),
        "candidate": candidate.model_dump(mode="json"),
        "reference": reference.model_dump(mode="json") if reference else None,
    }
    return write_run(runs_root, record, spec_json), record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--study", required=True, type=Path)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    if args.data_dir is None:
        from app.core.config import get_settings

        args.data_dir = Path(get_settings().research_data_dir)
    try:
        run_dir, record = run(args.study, args.data_dir)
    except ExperimentSpecError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"run recorded: {run_dir}")
    if record["status"] != "completed":
        print(f"FAILED: {record['error']}", file=sys.stderr)
        sys.exit(1)
    report = record["report"]
    if record["repeat_of"]:
        print(f"repeat of {len(record['repeat_of'])} earlier run(s) — not new evidence")
    print(f"development verdict: {report['verdict']}  (primary: {report['primary']['estimand']})")
    for reason in report["verdict_reasons"]:
        print(f"  - {reason}")
    print(f"observed mean: {report['primary']['mean']}")
    print("Exploratory development evidence only — not a validated model, not a trading signal.")


if __name__ == "__main__":
    main()
