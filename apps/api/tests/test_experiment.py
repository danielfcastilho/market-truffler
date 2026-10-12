import json
from datetime import timedelta

import pyarrow as pa
import pytest
from pydantic import ValidationError
from research_helpers import at, spec_dict, universe_rows, write_dataset

from app.research.dataset import DatasetIntegrityError
from app.research.experiment import (
    MIN_PERCENTILE_UNIVERSE,
    ExperimentSpec,
    ExperimentSpecError,
    hypothesis_hash,
    load_datasets,
    load_spec,
    select,
    spec_hash,
)

SHA = "0" * 64


def _spec(**overrides) -> ExperimentSpec:
    return ExperimentSpec.model_validate(spec_dict("ds", SHA, **overrides))


def _condition(**overrides):
    condition = {"feature": "return_24h", "transform": "raw", "op": "<", "value": 0.0}
    condition.update(overrides)
    return condition


def _hypothesis(*conditions, direction="long"):
    return {"statement": "s", "direction": direction, "conditions": list(conditions)}


# -- spec validation ---------------------------------------------------------------------


def test_a_valid_spec_parses():
    spec = _spec()
    assert spec.features == ["return_24h"]
    assert spec.horizon_label == "1h"


@pytest.mark.parametrize(
    "overrides",
    [
        {"unexpected": 1},  # unknown top-level field
        {"spec_version": 2},
        {"experiment_id": "Has Spaces"},
        {"label": "forward_return"},  # no horizon
        {"label": "rsi_14_5m"},  # a feature is not an outcome label
        {"datasets": []},
        {"datasets": [{"name": "a", "data_sha256": SHA}, {"name": "a", "data_sha256": SHA}]},
        {"datasets": [{"name": "../escape", "data_sha256": SHA}]},
        {"datasets": [{"name": "a", "data_sha256": "not-a-hash"}]},
        {"hypothesis": _hypothesis(_condition(), direction="sideways")},
        {"hypothesis": _hypothesis()},  # no conditions
        {"hypothesis": _hypothesis(*[_condition()] * 6)},  # too many
        {"hypothesis": _hypothesis(_condition(op="=="))},
        {"hypothesis": _hypothesis(_condition(transform="zscore"))},
        {"hypothesis": _hypothesis(_condition(value=float("nan")))},
        {"hypothesis": _hypothesis(_condition(transform="cs_percentile", value=1.5))},
        {"hypothesis": _hypothesis(_condition(code="__import__('os')"))},  # no code, ever
    ],
)
def test_invalid_specs_are_rejected(overrides):
    with pytest.raises(ValidationError):
        _spec(**overrides)


def test_load_spec_wraps_validation_errors(tmp_path):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps({"spec_version": 1}))
    with pytest.raises(ExperimentSpecError, match="invalid experiment spec"):
        load_spec(path)


def test_spec_hash_is_stable_and_content_sensitive():
    assert spec_hash(_spec()) == spec_hash(_spec())
    assert spec_hash(_spec()) != spec_hash(_spec(label="forward_relative_return_4h"))


def test_hypothesis_hash_identifies_the_claim_across_experiments():
    """Same direction + conditions = same hypothesis, whatever the
    experiment id, wording, label, or datasets."""
    a = _spec()
    b = _spec(experiment_id="other-v1", label="forward_relative_return_4h")
    assert hypothesis_hash(a) == hypothesis_hash(b)
    c = _spec(hypothesis=_hypothesis(_condition(), direction="short"))
    assert hypothesis_hash(a) != hypothesis_hash(c)


# -- dataset loading ---------------------------------------------------------------------


def _loadable(tmp_path, name="ds", rows=None, **kwargs):
    rows = rows if rows is not None else universe_rows(at(19), 60)
    manifest = write_dataset(tmp_path / name, rows, **kwargs)
    return manifest["data_sha256"]


def test_load_datasets_returns_only_needed_columns(tmp_path):
    sha = _loadable(tmp_path)
    loaded = load_datasets(ExperimentSpec.model_validate(spec_dict("ds", sha)), tmp_path)
    assert loaded.table.column_names == [
        "frame_time",
        "instrument_id",
        "symbol",
        "return_24h",
        "forward_relative_return_1h",
    ]
    assert loaded.horizon_minutes == 60
    assert loaded.sample_interval_minutes == 5


def test_a_spec_pinned_to_a_different_hash_is_refused(tmp_path):
    _loadable(tmp_path)
    with pytest.raises(ExperimentSpecError, match="pins data_sha256"):
        load_datasets(ExperimentSpec.model_validate(spec_dict("ds", SHA)), tmp_path)


def test_a_tampered_dataset_is_refused(tmp_path):
    sha = _loadable(tmp_path)
    with (tmp_path / "ds" / "data.parquet").open("ab") as handle:
        handle.write(b"\0")
    with pytest.raises(DatasetIntegrityError):
        load_datasets(ExperimentSpec.model_validate(spec_dict("ds", sha)), tmp_path)


def test_unknown_features_and_missing_horizons_are_refused(tmp_path):
    sha = _loadable(tmp_path)
    unknown = spec_dict("ds", sha, hypothesis=_hypothesis(_condition(feature="moon_phase")))
    with pytest.raises(ExperimentSpecError, match="unknown features"):
        load_datasets(ExperimentSpec.model_validate(unknown), tmp_path)
    no_horizon = spec_dict("ds", sha, label="forward_relative_return_4h")
    with pytest.raises(ExperimentSpecError, match="no 4h horizon"):
        load_datasets(ExperimentSpec.model_validate(no_horizon), tmp_path)


def test_datasets_with_different_sampling_cadences_are_never_combined(tmp_path):
    a = _loadable(tmp_path, "a", universe_rows(at(19), 60), sample_interval_minutes=5)
    b = _loadable(tmp_path, "b", universe_rows(at(20), 60), sample_interval_minutes=15)
    spec = spec_dict("a", a)
    spec["datasets"].append({"name": "b", "data_sha256": b})
    with pytest.raises(ExperimentSpecError, match="cadences"):
        load_datasets(ExperimentSpec.model_validate(spec), tmp_path)


def test_overlapping_datasets_are_refused_and_disjoint_ones_concatenate_in_time_order(tmp_path):
    later = _loadable(tmp_path, "later", universe_rows(at(20), 60))
    earlier = _loadable(tmp_path, "earlier", universe_rows(at(19), 60))
    dup = _loadable(tmp_path, "dup", universe_rows(at(20), 60))

    spec = spec_dict("later", later)
    spec["datasets"].append({"name": "earlier", "data_sha256": earlier})
    loaded = load_datasets(ExperimentSpec.model_validate(spec), tmp_path)
    times = loaded.table.column("frame_time").to_pylist()
    assert times == sorted(times) and len(times) == 120

    spec["datasets"].append({"name": "dup", "data_sha256": dup})
    with pytest.raises(ExperimentSpecError, match="overlap"):
        load_datasets(ExperimentSpec.model_validate(spec), tmp_path)


# -- selection ---------------------------------------------------------------------------


def _table(rows):
    columns = ["frame_time", "instrument_id", "symbol", "return_24h", "rsi_14_1h"]
    columns.append("forward_relative_return_1h")
    data = {c: [r.get(c) for r in rows] for c in columns}
    data["instrument_id"] = [int(r["symbol"][1:]) for r in rows]
    return pa.table(data)


def test_cs_percentile_selects_the_bottom_decile_and_treats_ties_identically():
    rows = universe_rows(at(19), 100)  # value k -> percentile (k + 1) / 100
    obs = select(_spec(), _table(rows))
    assert [s for s, sel in zip(obs.symbol, obs.selected, strict=True) if sel] == [
        f"S{k:03d}" for k in range(10)
    ]

    # Tie S010 with S009: both now have percentile count(<= 9.0) / 100 = 0.11,
    # so both fall outside the bottom decile together — tied values always
    # share one fate.
    rows[10]["return_24h"] = 9.0
    obs = select(_spec(), _table(rows))
    assert [s for s, sel in zip(obs.symbol, obs.selected, strict=True) if sel] == [
        f"S{k:03d}" for k in range(9)
    ]


def test_a_percentile_universe_below_the_minimum_selects_nothing():
    obs = select(_spec(), _table(universe_rows(at(19), MIN_PERCENTILE_UNIVERSE - 1)))
    assert not any(obs.selected)
    assert obs.timestamps_percentile_universe_too_small == 1


def test_selection_never_depends_on_future_label_availability():
    """Removing labels (as a delisting would) must not change a single
    percentile or selection — only eligibility."""
    rows = universe_rows(at(19), 100)
    with_labels = select(_spec(), _table(rows))
    for row in rows[::3]:
        row["forward_relative_return_1h"] = None
    without = select(_spec(), _table(rows))
    assert without.selected == with_labels.selected
    assert without.eligible != with_labels.eligible


def test_null_features_are_never_selected_nor_eligible_and_shrink_the_universe():
    rows = universe_rows(at(19), 100)
    for row in rows[:5]:
        row["return_24h"] = None  # the 5 lowest values disappear
    obs = select(_spec(), _table(rows))
    assert not any(obs.selected[:5]) and not any(obs.eligible[:5])
    # Universe is now 95 rows: values 5..14 -> percentiles 1/95..10/95 <= 0.1, 15 -> 11/95.
    assert [obs.symbol[i] for i, s in enumerate(obs.selected) if s] == [
        f"S{k:03d}" for k in range(5, 14)
    ]


def test_raw_conditions_combine_with_and():
    rows = universe_rows(at(19), 60)
    for row in rows:
        row["rsi_14_1h"] = 20.0 if int(row["symbol"][1:]) % 2 == 0 else 60.0
    spec = _spec(
        hypothesis=_hypothesis(
            _condition(feature="return_24h", op="<", value=10.0),
            _condition(feature="rsi_14_1h", op="<=", value=30.0),
        )
    )
    obs = select(spec, _table(rows))
    assert [obs.symbol[i] for i, s in enumerate(obs.selected) if s] == [
        "S000",
        "S002",
        "S004",
        "S006",
        "S008",
    ]


def test_short_direction_negates_outcomes_without_changing_selection():
    rows = universe_rows(at(19), 60)
    long = select(_spec(), _table(rows))
    short = select(_spec(hypothesis=_hypothesis(_condition(), direction="short")), _table(rows))
    long_pct = select(_spec(), _table(rows))
    assert long.selected == long_pct.selected
    assert all(
        (a is None and b is None) or a == -b
        for a, b in zip(long.outcome, short.outcome, strict=True)
    )


def test_selection_is_per_timestamp():
    rows = universe_rows(at(19), 60) + universe_rows(at(19) + timedelta(minutes=5), 60)
    obs = select(_spec(), _table(rows))
    assert sum(obs.selected) == 12  # 6 per timestamp (percentile <= 0.1 of 60)
