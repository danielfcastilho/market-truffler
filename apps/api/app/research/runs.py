"""OINK CORP experiment run records.

Each run is one immutable directory — the only source of truth, no
separate ledger to drift out of sync:

    <data_dir>/runs/<experiment_id>/<run_id>/spec.json
                                            /result.json

A run is written into a hidden sibling temp directory and atomically
renamed into place, so a crash never leaves a half-written run that looks
complete; leftover `.*.tmp-*` directories are ignored by `load_runs` and
reported. A run directory is never overwritten, and failed runs are
recorded too (with their error).

History is a directory scan. It enforces experiment identity (an
`experiment_id` can't silently change meaning: a different `spec_hash`
under an existing id is refused — publish a new id instead) and measures
validation reuse (how many earlier validation runs already looked at the
same validation days).
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.research.experiment import ExperimentSpecError

RUNS_DIRNAME = "runs"


def new_run_id(now: datetime) -> str:
    return f"{now:%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:6]}"


@dataclass(frozen=True)
class RunHistory:
    records: list[dict[str, Any]]
    incomplete_dirs: list[Path]


def load_runs(runs_root: Path) -> RunHistory:
    records: list[dict[str, Any]] = []
    incomplete: list[Path] = []
    if not runs_root.exists():
        return RunHistory(records, incomplete)
    for experiment_dir in sorted(p for p in runs_root.iterdir() if p.is_dir()):
        for run_dir in sorted(p for p in experiment_dir.iterdir() if p.is_dir()):
            if run_dir.name.startswith("."):
                incomplete.append(run_dir)
                continue
            records.append(json.loads((run_dir / "result.json").read_text()))
    return RunHistory(records, incomplete)


def check_experiment_identity(history: RunHistory, experiment_id: str, spec_hash: str) -> None:
    previous = {r["spec_hash"] for r in history.records if r.get("experiment_id") == experiment_id}
    if previous and previous != {spec_hash}:
        raise ExperimentSpecError(
            f"experiment_id {experiment_id!r} was already run with a different spec "
            f"({sorted(previous)}); an experiment's definition is immutable — "
            "use a new experiment_id"
        )


def validation_reuse(
    history: RunHistory, *, validation_days: list[str], hypothesis_hash: str, label: str
) -> dict[str, Any]:
    """Earlier completed validation runs whose validation days overlap
    `validation_days`. Any overlap means this validation result is not
    independent of earlier adaptive experimentation."""
    days = set(validation_days)
    prior = [
        r
        for r in history.records
        if r.get("status") == "completed"
        and r.get("segment") == "validation"
        and days & set(r.get("report", {}).get("days", []))
    ]
    keys = {(r["hypothesis_hash"], r["label"]) for r in prior}
    own_key = (hypothesis_hash, label)
    return {
        "prior_validation_runs_overlapping": len(prior),
        "other_hypotheses_evaluated_on_these_days": len(keys - {own_key}),
        "same_hypothesis_prior_runs": sum(
            1 for r in prior if (r["hypothesis_hash"], r["label"]) == own_key
        ),
        "independent": not prior,
    }


def write_run(runs_root: Path, record: dict[str, Any], spec_json: dict[str, Any]) -> Path:
    run_dir = runs_root / record["experiment_id"] / record["run_id"]
    if run_dir.exists():
        raise FileExistsError(f"{run_dir} already exists — run records are never overwritten")
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = run_dir.parent / f".{run_dir.name}.tmp-{uuid4().hex[:8]}"
    tmp_dir.mkdir()
    (tmp_dir / "spec.json").write_text(json.dumps(spec_json, indent=2, sort_keys=True))
    (tmp_dir / "result.json").write_text(json.dumps(record, indent=2, sort_keys=True))
    tmp_dir.rename(run_dir)  # atomic; fails rather than replace a non-empty directory
    return run_dir
