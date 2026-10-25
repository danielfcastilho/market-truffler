"""Experimental Truffle qualification: explicit, versioned conditions an
already-ranked instrument must meet to be highlighted as a Truffle.

Downstream of ranking and separate from it: a qualification spec names the
ranking candidate it applies to, reads the same eligible cross-section of
features the ranking scored, and never changes a score or an order.
Ranking says how a coin compares with the others under a model;
qualification says whether it meets this spec's conditions; neither says
whether the result is useful (that is validation, OINK CORP's job, and has
not been done).

A spec has frame conditions (the snapshot must be fresh and the frame
nearly complete — checked by the caller, which knows the clock) and
instrument conditions, each one of:

- `percentile` — the instrument's cross-sectional percentile of a feature
  within the ranked universe (`app.ranking.scoring.transform`
  "cs_percentile": fraction of the universe with a value <= this one) is
  `at_most` / `at_least` a bound;
- `value` — the raw feature value is `at_most` / `at_least` a bound.

Bounds are inclusive. An instrument qualifies only if every condition
passes; a missing or non-finite input makes its condition fail as
"missing", never pass. Rank is not a condition: rank 1 can fail and the
whole universe can fail. Nothing is tuned: thresholds are written down in
the spec files and versioned with them.
"""

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.ranking.scoring import canonical_hash, is_finite, transform

_SLUG = r"^[a-z0-9][a-z0-9-]{0,79}$"
#: Version of the evaluation semantics below (percentile universe,
#: inclusive bounds, missing-fails). A prospective registration freezes it,
#: so a change here cannot silently alter what a registered spec means.
QUALIFICATION_EVALUATOR_VERSION = 1
EXPERIMENTAL_STATUS = (
    "experimental — manually specified thresholds, not optimized, not validated; "
    "a Truffle is not a trading signal"
)


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Condition(_Frozen):
    id: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    feature: str = Field(min_length=1, max_length=64)
    measure: Literal["percentile", "value"]
    at_most: float | None = None
    at_least: float | None = None
    rationale: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _one_bound(self) -> "Condition":
        bounds = [b for b in (self.at_most, self.at_least) if b is not None]
        if len(bounds) != 1 or not math.isfinite(bounds[0]):
            raise ValueError(f"{self.id}: exactly one finite bound (at_most or at_least)")
        if self.measure == "percentile" and not 0 < bounds[0] < 1:
            raise ValueError(f"{self.id}: a percentile bound lies strictly between 0 and 1")
        return self

    def passes(self, observed: float) -> bool:
        if self.at_most is not None:
            return observed <= self.at_most
        assert self.at_least is not None
        return observed >= self.at_least

    @property
    def threshold(self) -> dict[str, float]:
        if self.at_most is not None:
            return {"at_most": self.at_most}
        assert self.at_least is not None
        return {"at_least": self.at_least}


class QualificationSpec(_Frozen):
    spec_version: Literal[1]
    qualification_id: str = Field(pattern=_SLUG)
    name: str = Field(min_length=1, max_length=200)
    ranking_candidate: str = Field(pattern=_SLUG)
    direction: Literal["long", "short"]
    status: Literal["experimental"]
    #: "relative": every condition is relative to this frame's universe or
    #: a sign check — no claim about absolute opportunity quality.
    policy: Literal["relative"]
    description: str = Field(min_length=1, max_length=2000)
    min_frame_completeness: float = Field(gt=0, le=1)
    conditions: list[Condition] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def _unique_ids(self) -> "QualificationSpec":
        ids = [c.id for c in self.conditions]
        if len(ids) != len(set(ids)):
            raise ValueError("condition ids must be unique")
        return self

    @property
    def features(self) -> list[str]:
        return list(dict.fromkeys(c.feature for c in self.conditions))


def qualification_hash(spec: QualificationSpec) -> str:
    return canonical_hash(spec.model_dump(mode="json"))


def load_qualification(directory: Path, qualification_id: str) -> QualificationSpec:
    """The configured spec, or ValueError — never a substitute."""
    try:
        spec = QualificationSpec.model_validate_json(
            (directory / f"{qualification_id}.json").read_text()
        )
    except OSError as exc:
        raise ValueError(f"qualification {qualification_id} not found") from exc
    except ValidationError as exc:
        raise ValueError(
            f"qualification {qualification_id} is invalid: {exc.error_count()} errors"
        ) from exc
    if spec.qualification_id != qualification_id:
        raise ValueError(f"{qualification_id}.json declares {spec.qualification_id!r}")
    return spec


def spec_summary(spec: QualificationSpec) -> dict[str, Any]:
    return {
        "qualification_id": spec.qualification_id,
        "name": spec.name,
        "spec_hash": qualification_hash(spec),
        "ranking_candidate": spec.ranking_candidate,
        "direction": spec.direction,
        "policy": spec.policy,
        "description": spec.description,
        "min_frame_completeness": spec.min_frame_completeness,
        "conditions": [c.model_dump() for c in spec.conditions],
        "status": EXPERIMENTAL_STATUS,
    }


def _percentiles(values: Sequence[Any]) -> list[float | None]:
    """cs_percentile over the instruments with a finite value; None for
    the others (their percentile is undefined, not zero)."""
    finite = [i for i, v in enumerate(values) if is_finite(v)]
    out: list[float | None] = [None] * len(values)
    if finite:
        ranked, _ = transform("cs_percentile", [float(values[i]) for i in finite])
        for i, p in zip(finite, ranked, strict=True):
            out[i] = p
    return out


def evaluate(
    spec: QualificationSpec, symbols: Sequence[str], features: Mapping[str, Sequence[Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Every instrument condition for every instrument of the ranked
    universe: `{symbol: [check, ...]}` in the spec's condition order.
    `features` holds one value (or None) per symbol for each feature the
    spec names; a feature absent altogether counts as missing for all."""
    n = len(symbols)
    measured: dict[str, dict[str, list[Any]]] = {}
    for feature in spec.features:
        raw = list(features.get(feature, [None] * n))
        measured[feature] = {"value": raw, "percentile": _percentiles(raw)}
    out: dict[str, list[dict[str, Any]]] = {}
    for i, symbol in enumerate(symbols):
        checks: list[dict[str, Any]] = []
        for c in spec.conditions:
            observed = measured[c.feature][c.measure][i]
            if not is_finite(observed):
                checks.append(
                    {
                        "id": c.id,
                        "feature": c.feature,
                        "measure": c.measure,
                        "threshold": c.threshold,
                        "observed": None,
                        "outcome": "missing",
                    }
                )
                continue
            checks.append(
                {
                    "id": c.id,
                    "feature": c.feature,
                    "measure": c.measure,
                    "threshold": c.threshold,
                    "observed": float(observed),
                    "outcome": "passed" if c.passes(float(observed)) else "failed",
                }
            )
        out[symbol] = checks
    return out


def qualified(checks: Sequence[dict[str, Any]]) -> bool:
    return all(c["outcome"] == "passed" for c in checks)
