import json
from datetime import timedelta
from pathlib import Path

import pyarrow as pa
import pytest
from research_helpers import at, write_dataset

from app.research import evaluation
from app.research.campaign import campaign_hash, load_campaign
from app.research.comparison import check_nested, evaluate_incremental
from app.research.evaluation import SplitPlan, plan_split
from app.research.experiment import (
    ExperimentSpec,
    ExperimentSpecError,
    Observations,
    load_datasets,
    select,
)
from app.research.runs import load_runs
from app.scripts.run_campaign import run_comparison

REPO = Path(__file__).resolve().parents[3]
LABEL = "forward_relative_return_1h"
DOWN = {"feature": "return_1h", "transform": "cs_percentile", "op": "<=", "value": 0.2}
OI_DOWN = {"feature": "oi_change_1h", "transform": "raw", "op": "<", "value": 0.0}
OI_UP = {"feature": "oi_change_1h", "transform": "raw", "op": ">", "value": 0.0}
N_SYMBOLS = 60
PER_DAY = 20  # 15-minute timestamps per synthetic day


def _rows(days: int, *, oi_down_bonus: float = 0.01):
    """Each timestamp: 60 symbols whose return_1h ranks rotate (so many
    symbols get selected over time), Open Interest falling for odd ranks,
    and a next-hour outcome raised by `oi_down_bonus` where OI fell — plus a
    day-level wobble whose sign alternates, so without a bonus the extra
    condition carries no consistent information."""
    rows = []
    for d in range(days):
        wobble = 0.001 if d % 2 else -0.001
        for i in range(PER_DAY):
            t = at(1 + d) + timedelta(minutes=15 * i)
            for k in range(N_SYMBOLS):
                rank = (k + i + d) % N_SYMBOLS
                oi = -0.02 if rank % 2 else 0.03
                rows.append(
                    {
                        "frame_time": t,
                        "symbol": f"S{k:03d}",
                        "return_1h": float(rank),
                        "oi_change_1h": oi,
                        LABEL: (oi_down_bonus + wobble) if oi < 0 else 0.0,
                    }
                )
    return rows


def _spec(experiment_id: str, conditions, dataset, direction="long") -> dict:
    return {
        "spec_version": 1,
        "experiment_id": experiment_id,
        "heuristic": {"id": "test-heuristic", "text": "A test intuition."},
        "hypothesis": {"statement": "x", "direction": direction, "conditions": conditions},
        "datasets": [dataset],
        "label": LABEL,
    }


def _campaign(tmp_path: Path, rows) -> tuple[Path, Path, Path]:
    data = tmp_path / "data"
    manifest = write_dataset(data / "shard_test", rows, sample_interval_minutes=15)
    ref = {"name": "shard_test", "data_sha256": manifest["data_sha256"]}
    experiments = tmp_path / "experiments"
    experiments.mkdir()
    specs = {
        "base-long-v1": _spec("base-long-v1", [DOWN], ref),
        "h1-long-v1": _spec("h1-long-v1", [DOWN, OI_DOWN], ref),
    }
    for name, spec in specs.items():
        (experiments / f"{name}.json").write_text(json.dumps(spec))
    campaign = {
        "campaign_version": 1,
        "campaign_id": "test-campaign-v1",
        "created": "2026-10-24",
        "question": "q",
        "motivation": "m",
        "hypotheses": [
            {"id": "H1", "statement": "s", "prediction": "p", "experiment_id": "h1-long-v1"}
        ],
        "baselines": [
            {"id": "eligible-universe", "description": "built in"},
            {"id": "price-only", "description": "d", "experiment_id": "base-long-v1"},
        ],
        "comparisons": [
            {
                "comparison_id": "h1-vs-price-only-v1",
                "hypothesis": "H1",
                "candidate": "h1-long-v1",
                "reference": "base-long-v1",
            }
        ],
        "parameters_rationale": "r",
        "evaluation_plan": "e",
    }
    path = tmp_path / "campaigns" / "test-campaign-v1.json"
    path.parent.mkdir()
    path.write_text(json.dumps(campaign))
    return path, experiments, data


def _compare(tmp_path, rows):
    path, experiments, data = _campaign(tmp_path, rows)
    campaign, specs = load_campaign(path, experiments)
    return run_comparison(
        campaign,
        campaign_hash(campaign, specs),
        campaign.comparisons[0],
        specs,
        "development",
        data,
    )


# -- the registered campaign -----------------------------------------------------------------


def test_the_registered_campaign_and_its_specs_are_valid():
    campaign, specs = load_campaign(
        REPO / "research/oink_corp/campaigns/oi-incremental-price-1h-v1.json",
        REPO / "research/oink_corp/experiments",
    )
    assert [h.id for h in campaign.hypotheses] == ["H1", "H2", "H3", "H4"]
    assert len(campaign.comparisons) == 4 and len(specs) == 6
    assert {s.label for s in specs.values()} == {"forward_relative_return_1h"}


# -- nesting ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("candidate", "reference", "message"),
    [
        ({"conditions": [DOWN, OI_DOWN], "direction": "short"}, {}, "direction"),
        ({"conditions": [DOWN]}, {}, "at least one condition"),
        ({"conditions": [OI_DOWN, OI_UP]}, {}, "not among"),
        ({"conditions": [DOWN, OI_DOWN], "label": "forward_relative_return_4h"}, {}, "label"),
    ],
)
def test_only_strict_refinements_on_identical_data_can_be_compared(candidate, reference, message):
    dataset = {"name": "d", "data_sha256": "0" * 64}
    base = _spec("ref-v1", [DOWN], dataset)
    cand = _spec("cand-v1", candidate["conditions"], dataset, candidate.get("direction", "long"))
    cand["label"] = candidate.get("label", LABEL)
    with pytest.raises(ExperimentSpecError, match=message):
        check_nested(ExperimentSpec.model_validate(cand), ExperimentSpec.model_validate(base))


def test_a_campaign_naming_a_missing_or_mislabelled_spec_is_refused(tmp_path):
    path, experiments, _ = _campaign(tmp_path, _rows(1))
    (experiments / "h1-long-v1.json").unlink()
    with pytest.raises(FileNotFoundError):
        load_campaign(path, experiments)
    spec = json.loads((experiments / "base-long-v1.json").read_text())
    spec["experiment_id"] = "another-v1"
    (experiments / "h1-long-v1.json").write_text(json.dumps(spec))
    with pytest.raises(ExperimentSpecError, match="declares"):
        load_campaign(path, experiments)


# -- the comparison -------------------------------------------------------------------------------


def test_information_added_by_the_extra_condition_is_detected(tmp_path):
    _, record = _compare(tmp_path, _rows(14))
    report = record["report"]
    assert record["status"] == "completed" and report["verdict"] == "positive_evidence"
    assert report["spread"]["mean"] > 0
    assert report["counts"]["timestamps_with_selection"] == 200  # 10 development days
    assert report["uncertainty"]["interval"][0] > 0


def test_a_harmful_extra_condition_is_negative_evidence(tmp_path):
    _, record = _compare(tmp_path, _rows(14, oi_down_bonus=-0.01))
    assert record["report"]["verdict"] == "negative_evidence"


def test_an_uninformative_extra_condition_is_not_evidence(tmp_path):
    _, record = _compare(tmp_path, _rows(14, oi_down_bonus=0.0))
    assert record["report"]["verdict"] == "inconclusive"


def test_few_days_are_insufficient_and_the_run_is_recorded(tmp_path):
    run_dir, record = _compare(tmp_path, _rows(2))
    report = record["report"]
    assert report["verdict"] == "insufficient_data"
    assert report["uncertainty"]["suppressed"] is True
    assert any("qualifying days" in r for r in report["verdict_reasons"])
    assert run_dir.parent.name == "h1-vs-price-only-v1"
    saved = load_runs(tmp_path / "data" / "runs").records[0]
    assert saved["kind"] == "incremental_comparison" and saved["campaign_hash"]
    assert saved["candidate"]["experiment_id"] == "h1-long-v1"


def test_a_comparison_id_cannot_change_meaning(tmp_path):
    path, experiments, data = _campaign(tmp_path, _rows(1))
    campaign, specs = load_campaign(path, experiments)
    c = campaign.comparisons[0]
    run_comparison(campaign, campaign_hash(campaign, specs), c, specs, "development", data)
    changed = json.loads((experiments / "h1-long-v1.json").read_text())
    changed["hypothesis"]["statement"] = "reworded"
    (experiments / "h1-long-v1.json").write_text(json.dumps(changed))
    campaign, specs = load_campaign(path, experiments)
    with pytest.raises(ExperimentSpecError, match="immutable"):
        run_comparison(campaign, campaign_hash(campaign, specs), c, specs, "development", data)


def test_a_tampered_dataset_fails_the_run_instead_of_evaluating(tmp_path):
    path, experiments, data = _campaign(tmp_path, _rows(1))
    target = data / "shard_test" / "data.parquet"
    target.write_bytes(target.read_bytes()[:-3])
    campaign, specs = load_campaign(path, experiments)
    _, record = run_comparison(
        campaign,
        campaign_hash(campaign, specs),
        campaign.comparisons[0],
        specs,
        "development",
        data,
    )
    assert record["status"] == "failed" and "report" not in record


def test_reference_rows_missing_a_candidate_feature_are_excluded_from_both_sides():
    t = at(1)
    split = SplitPlan([t.date()], [], [t.date()], [], None)
    # Row 0 would be reference-selected with a huge outcome but has no OI:
    # it must not drag the reference mean.
    reference = Observations(
        [t] * 3, ["A", "B", "C"], [True, True, True], [True] * 3, [10.0, 1.0, 0.0], 0
    )
    candidate = Observations(
        [t] * 3, ["A", "B", "C"], [False, True, False], [False, True, True], [None, 1.0, 0.0], 0
    )
    report = evaluate_incremental(
        candidate, reference, segment="development", split=split, horizon_minutes=60, seed=1
    )
    assert report["outcomes"]["reference_mean"] == 0.5
    assert report["spread"]["mean"] == 0.5
    assert report["counts"]["reference_rows_at_those_timestamps"] == 2


def test_non_nested_observations_are_refused():
    t = at(1)
    split = SplitPlan([t.date()], [], [t.date()], [], None)
    reference = Observations([t], ["A"], [False], [True], [1.0], 0)
    candidate = Observations([t], ["A"], [True], [True], [1.0], 0)
    with pytest.raises(ValueError, match="not nested"):
        evaluate_incremental(
            candidate, reference, segment="development", split=split, horizon_minutes=60, seed=1
        )


def test_selection_for_both_sides_never_depends_on_outcomes(tmp_path):
    path, experiments, data = _campaign(tmp_path, _rows(2))
    campaign, specs = load_campaign(path, experiments)
    table = load_datasets(specs["h1-long-v1"], data).table
    reversed_labels = table.set_column(
        table.column_names.index(LABEL), LABEL, pa.array(table.column(LABEL).to_pylist()[::-1])
    )
    for spec in specs.values():
        assert select(spec, table).selected == select(spec, reversed_labels).selected


def test_the_evaluator_protocol_is_unchanged():
    """The comparison reuses Evaluator v1's rules; it never modifies them."""
    assert evaluation.EVALUATOR_VERSION == 1
    assert (evaluation.MIN_DAYS_FOR_INFERENCE, evaluation.MIN_SELECTED_TIMESTAMPS) == (10, 200)
    assert plan_split([at(1)] * 50).validation_days == []
