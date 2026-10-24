"""OINK CORP experiment specification and deterministic execution.

Identities (no registry — they live in the spec and in run records):

- **heuristic** — a human intuition, as text plus a slug that groups the
  hypotheses derived from it;
- **hypothesis** — a measurable interpretation: direction + conditions,
  identified by `hypothesis_hash` (so the same hypothesis is recognizable
  across experiments, datasets and horizons);
- **experiment** — a hypothesis evaluated on specific datasets and one
  outcome label, identified by an immutable `experiment_id` and
  `spec_hash`;
- **run** — one execution of an experiment (see `app.research.runs`).

A spec is pure data: a fixed set of fields, operators and transforms —
never executable code, never evaluation settings (those belong to
`app.research.evaluation`, which the spec cannot influence).

Execution reads only materialized, hash-verified Parquet datasets — no
database, no replay, no feature recomputation. Selection uses only values
known at each decision timestamp: a `cs_percentile` is computed over the
rows whose *feature* is available at that `frame_time`, never filtered by
whether a (future) label exists.
"""

import bisect
import hashlib
import json
import math
import operator
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.research.dataset import read_dataset
from app.research.reservations import Access, ReservedDataError, check_access

SPEC_VERSION = 1
MAX_CONDITIONS = 5
#: A cross-sectional percentile needs a real cross-section: at a timestamp
#: with fewer feature-available rows than this, nothing is selected (and
#: the timestamp is counted in `timestamps_percentile_universe_too_small`).
MIN_PERCENTILE_UNIVERSE = 50

_SLUG = r"^[a-z0-9][a-z0-9-]{0,79}$"
_DATASET_NAME = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
_LABEL_RE = re.compile(r"^forward_(?:relative_)?return_(\d+[mhd])$")
_OPS: dict[str, Callable[[float, float], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


class ExperimentSpecError(ValueError):
    """An invalid spec, or one incompatible with its datasets."""


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Condition(_Frozen):
    feature: str = Field(min_length=1, max_length=64)
    transform: Literal["raw", "cs_percentile"]
    op: Literal["<", "<=", ">", ">="]
    value: float

    @model_validator(mode="after")
    def _check_value(self) -> "Condition":
        if not math.isfinite(self.value):
            raise ValueError("value must be finite")
        if self.transform == "cs_percentile" and not 0.0 <= self.value <= 1.0:
            raise ValueError("a cs_percentile value must be within [0, 1]")
        return self


class Heuristic(_Frozen):
    id: str = Field(pattern=_SLUG)
    text: str = Field(min_length=1, max_length=2000)


class Hypothesis(_Frozen):
    statement: str = Field(min_length=1, max_length=2000)
    direction: Literal["long", "short"]
    conditions: list[Condition] = Field(min_length=1, max_length=MAX_CONDITIONS)


class DatasetRef(_Frozen):
    name: str = Field(pattern=_DATASET_NAME)
    data_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ExperimentSpec(_Frozen):
    spec_version: Literal[1]
    experiment_id: str = Field(pattern=_SLUG)
    heuristic: Heuristic
    hypothesis: Hypothesis
    datasets: list[DatasetRef] = Field(min_length=1)
    label: str

    @field_validator("label")
    @classmethod
    def _check_label(cls, value: str) -> str:
        if not _LABEL_RE.match(value):
            raise ValueError("label must be forward_return_<H> or forward_relative_return_<H>")
        return value

    @model_validator(mode="after")
    def _check_unique_datasets(self) -> "ExperimentSpec":
        names = [ref.name for ref in self.datasets]
        if len(names) != len(set(names)):
            raise ValueError("datasets must not repeat")
        return self

    @property
    def horizon_label(self) -> str:
        match = _LABEL_RE.match(self.label)
        assert match is not None  # guaranteed by _check_label
        return match.group(1)

    @property
    def features(self) -> list[str]:
        """Referenced features, deduplicated, in first-use order."""
        return list(dict.fromkeys(c.feature for c in self.hypothesis.conditions))


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def spec_hash(spec: ExperimentSpec) -> str:
    return _canonical_hash(spec.model_dump(mode="json"))


def hypothesis_hash(spec: ExperimentSpec) -> str:
    """Direction + conditions only — the measurable claim, independent of
    wording, datasets, label, or experiment id."""
    hypothesis = spec.hypothesis.model_dump(mode="json")
    return _canonical_hash(
        {"direction": hypothesis["direction"], "conditions": hypothesis["conditions"]}
    )


def load_spec(path: Path) -> ExperimentSpec:
    try:
        return ExperimentSpec.model_validate_json(path.read_text())
    except ValidationError as exc:
        raise ExperimentSpecError(f"invalid experiment spec {path}: {exc}") from exc


# -- dataset loading -------------------------------------------------------------------


@dataclass(frozen=True)
class LoadedDatasets:
    manifests: list[dict[str, Any]]
    table: pa.Table  # frame_time, instrument_id, symbol, <features>, <label>; time-ordered
    sample_interval_minutes: int
    horizon_minutes: int


def load_datasets(
    spec: ExperimentSpec, data_dir: Path, *, access: Access | None = None
) -> LoadedDatasets:
    """Load and validate every dataset the spec names: content hash (both
    the manifest's own record and the spec's pin), schema version, feature
    and label availability, one shared sampling cadence (mixing cadences
    would silently change observation weighting), one shared label
    horizon, and non-overlapping time ranges. Returns one time-ordered
    table with only the needed columns.

    Before any Parquet file (and so any outcome label) is read, refuses
    datasets reaching into data reserved for a prospective evaluation
    (`app.research.reservations`) unless `access` is that evaluation's
    live grant."""
    manifests_by_name = {
        ref.name: json.loads((data_dir / ref.name / "manifest.json").read_text())
        for ref in spec.datasets
    }
    try:
        check_access(data_dir, list(manifests_by_name.items()), access)
    except ReservedDataError as exc:
        raise ExperimentSpecError(str(exc)) from exc
    loaded: list[tuple[dict[str, Any], pa.Table]] = []
    for ref in spec.datasets:
        manifest = manifests_by_name[ref.name]
        if manifest.get("data_sha256") != ref.data_sha256:
            raise ExperimentSpecError(
                f"dataset {ref.name}: spec pins data_sha256 {ref.data_sha256}, "
                f"manifest records {manifest.get('data_sha256')!r}"
            )
        unknown = [f for f in spec.features if f not in manifest["feature_names"]]
        if unknown:
            raise ExperimentSpecError(f"dataset {ref.name}: unknown features {unknown}")
        if spec.horizon_label not in manifest["horizons_minutes"]:
            raise ExperimentSpecError(
                f"dataset {ref.name}: no {spec.horizon_label} horizon "
                f"(has {sorted(manifest['horizons_minutes'])})"
            )
        columns = ["frame_time", "instrument_id", "symbol", *spec.features, spec.label]
        loaded.append(read_dataset(data_dir / ref.name, columns=columns))  # verifies the hash

    manifests = [manifest for manifest, _ in loaded]
    cadences = {m["sample_interval_minutes"] for m in manifests}
    if len(cadences) != 1:
        raise ExperimentSpecError(f"datasets mix sampling cadences {sorted(cadences)}")
    horizons = {m["horizons_minutes"][spec.horizon_label] for m in manifests}
    if len(horizons) != 1:
        raise ExperimentSpecError(f"datasets disagree on the {spec.horizon_label} horizon")

    ordered = sorted(
        (pair for pair in loaded if pair[1].num_rows > 0),
        key=lambda pair: pair[0]["first_frame_time"],
    )
    for (earlier, _), (later, _) in zip(ordered, ordered[1:], strict=False):
        if later["first_frame_time"] <= earlier["last_frame_time"]:
            raise ExperimentSpecError("datasets overlap in time")

    tables = [table for _, table in ordered] or [loaded[0][1]]
    table = pa.concat_tables(tables)
    table = table.sort_by([("frame_time", "ascending"), ("instrument_id", "ascending")])
    return LoadedDatasets(
        manifests=manifests,
        table=table,
        sample_interval_minutes=cadences.pop(),
        horizon_minutes=horizons.pop(),
    )


# -- selection ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Observations:
    """One entry per dataset row, time-ordered. `selected` is decided from
    point-in-time features only; `eligible` additionally requires every
    referenced feature and the label to be present; `outcome` is the
    label in the hypothesis' direction (negated for Short), or None."""

    frame_time: list[datetime]
    symbol: list[str]
    selected: list[bool]
    eligible: list[bool]
    outcome: list[float | None]
    timestamps_percentile_universe_too_small: int


def _percentile_lookup(values: list[float | None]) -> Callable[[float], float] | None:
    available = sorted(v for v in values if v is not None)
    if len(available) < MIN_PERCENTILE_UNIVERSE:
        return None
    n = len(available)
    # Fraction of the feature-available universe with value <= v — so tied
    # values all receive the same (inclusive) percentile.
    return lambda v: bisect.bisect_right(available, v) / n


def select(spec: ExperimentSpec, table: pa.Table) -> Observations:
    frame_times: list[datetime] = table.column("frame_time").to_pylist()
    symbols: list[str] = table.column("symbol").to_pylist()
    feature_values = {f: table.column(f).to_pylist() for f in spec.features}
    labels: list[float | None] = table.column(spec.label).to_pylist()
    sign = 1.0 if spec.hypothesis.direction == "long" else -1.0

    n = len(frame_times)
    selected = [False] * n
    eligible = [False] * n
    outcome: list[float | None] = [None] * n
    too_small = 0

    start = 0
    while start < n:
        end = start
        while end < n and frame_times[end] == frame_times[start]:
            end += 1
        rows = range(start, end)

        conditions_ok = [True] * (end - start)
        universe_too_small = False
        for condition in spec.hypothesis.conditions:
            values = feature_values[condition.feature][start:end]
            compare = _OPS[condition.op]
            if condition.transform == "raw":
                passes = [v is not None and compare(v, condition.value) for v in values]
            else:
                percentile = _percentile_lookup(values)
                if percentile is None:
                    universe_too_small = True
                    passes = [False] * len(values)
                else:
                    passes = [
                        v is not None and compare(percentile(v), condition.value) for v in values
                    ]
            conditions_ok = [a and b for a, b in zip(conditions_ok, passes, strict=True)]
        too_small += universe_too_small

        for offset, i in enumerate(rows):
            selected[i] = conditions_ok[offset]
            label = labels[i]
            features_present = all(feature_values[f][i] is not None for f in spec.features)
            if features_present and label is not None:
                eligible[i] = True
                outcome[i] = sign * label
        start = end

    return Observations(
        frame_time=frame_times,
        symbol=symbols,
        selected=selected,
        eligible=eligible,
        outcome=outcome,
        timestamps_percentile_universe_too_small=too_small,
    )
