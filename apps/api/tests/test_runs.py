import json

import pytest
from research_helpers import at, daily_grid, spec_dict, universe_rows, write_dataset

from app.research.experiment import ExperimentSpecError
from app.research.runs import (
    RunHistory,
    check_experiment_identity,
    load_runs,
    validation_reuse,
    write_run,
)
from app.scripts.run_experiment import run


def _seed(tmp_path, n_days=6, timestamps_per_day=12):
    """A small synthetic dataset with n_days qualifying days (mechanics
    only — not market evidence)."""
    rows = [
        row
        for day in range(1, n_days + 1)
        for t in daily_grid(day, timestamps_per_day)
        for row in universe_rows(t, 60)
    ]
    manifest = write_dataset(tmp_path / "ds", rows)
    return manifest["data_sha256"]


def _write_spec(tmp_path, sha, name="spec.json", **overrides):
    path = tmp_path / name
    path.write_text(json.dumps(spec_dict("ds", sha, **overrides)))
    return path


def _without_run_identity(record):
    return {k: v for k, v in record.items() if k not in ("run_id", "executed_at")}


# -- end to end -----------------------------------------------------------------------------


def test_a_development_run_is_recorded_with_full_provenance(tmp_path):
    sha = _seed(tmp_path)
    run_dir, record = run(_write_spec(tmp_path, sha), "development", tmp_path)

    assert run_dir.parent == tmp_path / "runs" / "test-experiment-v1"
    stored = json.loads((run_dir / "result.json").read_text())
    assert stored == record
    assert json.loads((run_dir / "spec.json").read_text())["experiment_id"] == "test-experiment-v1"
    assert record["status"] == "completed" and record["error"] is None
    for key in ("spec_hash", "hypothesis_hash", "evaluator_version", "code_fingerprint", "seed"):
        assert record[key] is not None
    assert record["datasets"][0]["data_sha256"] == sha
    assert record["report"]["segment"] == "development"
    assert record["report"]["days"] == [f"2026-09-0{d}" for d in (1, 2, 3)]
    # Synthetic data has 3 development days: never enough for inference.
    assert record["report"]["verdict"] == "insufficient_data"
    assert record["report"]["uncertainty"]["suppressed"] is True


def test_rerunning_is_deterministic_and_never_overwrites(tmp_path):
    sha = _seed(tmp_path)
    spec = _write_spec(tmp_path, sha)
    first_dir, first = run(spec, "development", tmp_path)
    second_dir, second = run(spec, "development", tmp_path)
    assert first_dir != second_dir
    assert _without_run_identity(first) == _without_run_identity(second)
    assert json.loads((first_dir / "result.json").read_text()) == first  # untouched


def test_an_experiment_id_cannot_change_meaning(tmp_path):
    sha = _seed(tmp_path)
    run(_write_spec(tmp_path, sha), "development", tmp_path)
    edited = _write_spec(tmp_path, sha, name="edited.json", label="forward_return_1h")
    with pytest.raises(ExperimentSpecError, match="immutable"):
        run(edited, "development", tmp_path)
    renamed = _write_spec(
        tmp_path, sha, name="renamed.json", experiment_id="test-experiment-v2",
        label="forward_return_1h",
    )  # fmt: skip
    _, record = run(renamed, "development", tmp_path)
    assert record["status"] == "completed"


def test_a_failed_run_is_recorded_with_its_error(tmp_path):
    _seed(tmp_path)
    run_dir, record = run(_write_spec(tmp_path, "0" * 64), "development", tmp_path)
    assert record["status"] == "failed"
    assert "pins data_sha256" in record["error"]
    assert json.loads((run_dir / "result.json").read_text())["status"] == "failed"


def test_validation_runs_track_reuse_across_experiments(tmp_path):
    sha = _seed(tmp_path)
    _, first = run(_write_spec(tmp_path, sha), "validation", tmp_path)
    reuse = first["report"]["validation_reuse"]
    assert reuse["independent"] is True and reuse["prior_validation_runs_overlapping"] == 0

    other = _write_spec(
        tmp_path, sha, name="other.json", experiment_id="other-v1",
        hypothesis={"statement": "s", "direction": "short", "conditions": [
            {"feature": "return_24h", "transform": "raw", "op": ">", "value": 30.0}
        ]},
    )  # fmt: skip
    _, second = run(other, "validation", tmp_path)
    reuse = second["report"]["validation_reuse"]
    assert reuse["independent"] is False
    assert reuse["prior_validation_runs_overlapping"] == 1
    assert reuse["other_hypotheses_evaluated_on_these_days"] == 1

    # Development runs never count as validation use.
    run(
        _write_spec(tmp_path, sha, name="dev.json", experiment_id="dev-v1"), "development", tmp_path
    )
    _, third = run(_write_spec(tmp_path, sha), "validation", tmp_path)
    assert third["report"]["validation_reuse"]["prior_validation_runs_overlapping"] == 2
    assert third["report"]["validation_reuse"]["same_hypothesis_prior_runs"] == 1


def test_validation_without_enough_days_is_recorded_as_insufficient(tmp_path):
    sha = _seed(tmp_path, n_days=5)
    _, record = run(_write_spec(tmp_path, sha), "validation", tmp_path)
    assert record["status"] == "completed"
    assert record["report"]["verdict"] == "insufficient_data"
    assert record["report"]["days"] == []


def test_selection_counts_survive_missing_labels(tmp_path):
    rows = [row for t in daily_grid(1, 12) for row in universe_rows(t, 60)]
    for row in rows:
        if row["symbol"] == "S000":  # always selected (lowest return_24h)
            row["forward_relative_return_1h"] = None
    sha = write_dataset(tmp_path / "ds", rows)["data_sha256"]
    _, record = run(_write_spec(tmp_path, sha), "development", tmp_path)
    counts = record["report"]["counts"]
    assert counts["selected_at_decision"] == 12 * 6
    assert counts["selected_missing_outcome"] == 12
    assert counts["selected_with_outcome"] == 12 * 5


# -- run storage ---------------------------------------------------------------------------


def test_write_run_refuses_to_overwrite(tmp_path):
    record = {"experiment_id": "e-v1", "run_id": "r1", "spec_hash": "h"}
    write_run(tmp_path, record, {})
    with pytest.raises(FileExistsError):
        write_run(tmp_path, record, {})


def test_load_runs_ignores_and_reports_incomplete_runs(tmp_path):
    write_run(tmp_path, {"experiment_id": "e-v1", "run_id": "r1", "spec_hash": "h"}, {})
    (tmp_path / "e-v1" / ".r2.tmp-abcd").mkdir()  # a crash mid-write
    history = load_runs(tmp_path)
    assert [r["run_id"] for r in history.records] == ["r1"]
    assert [p.name for p in history.incomplete_dirs] == [".r2.tmp-abcd"]


def test_identity_and_reuse_helpers_on_an_empty_history():
    empty = RunHistory([], [])
    check_experiment_identity(empty, "e-v1", "anything")
    assert validation_reuse(empty, validation_days=["2026-09-04"], hypothesis_hash="h", label="l")[
        "independent"
    ]


def test_development_purges_labels_crossing_into_validation(tmp_path):
    """End-to-end purge: with 6 qualifying days (3 dev / 3 validation) and
    a 1h label, the last hour of development timestamps before midnight of
    the first validation day is purged."""
    rows = [row for d in range(1, 7) for t in daily_grid(d, 12) for row in universe_rows(t, 60)]
    late = at(3, 23, 10)  # + 1h crosses into day 4 (validation)
    rows += universe_rows(late, 60)
    sha = write_dataset(tmp_path / "ds", rows)["data_sha256"]
    _, record = run(_write_spec(tmp_path, sha), "development", tmp_path)
    assert record["report"]["purged_rows"] == 60
    assert record["report"]["purge_boundary"] == "2026-09-04T00:00:00+00:00"
    # Day 3 stays in development; only its crossing timestamp is gone.
    assert "2026-09-03" in record["report"]["days"]
    assert record["report"]["counts"]["timestamps"] == 3 * 12
