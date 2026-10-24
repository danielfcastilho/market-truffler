"""The one implementation of ranking candidates and their scoring, shared by
OINK CORP's historical ranking research (`app.research.ranking`) and the
live Sniffer rankings (`app.services.live_rankings`).

It works on features only: nothing here can see an outcome label, a
dataset or the database — callers hand it one cross-section of feature
values. That is what lets the live runtime use it without depending on
research code (`tests/test_architecture_boundaries.py`), and what makes
historical and live rankings identical for identical inputs.

Model family `linear_weighted_v1`:

    S_i = sum_j w_j * T_j(f_j(i))

- transforms over the decision universe of one timestamp: `identity`;
  `cs_percentile` (fraction of the universe with value <= this one, ties
  share a value); `cs_robust_z` ((v - median) / (1.4826 * MAD); a zero-MAD
  feature contributes 0 and is reported as degenerate);
- eligibility (`missing_policy: exclude`): every required feature present
  and finite;
- order: score descending, ties broken by symbol ascending;
- a cross-section smaller than `MIN_CROSS_SECTION` is not ranked.
"""

import bisect
import hashlib
import json
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MIN_CROSS_SECTION = 50
MAX_TERMS = 10
_SLUG = r"^[a-z0-9][a-z0-9-]{0,79}$"
_MAD_SCALE = 1.4826


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FeatureTerm(_Frozen):
    feature: str = Field(min_length=1, max_length=64)
    transform: Literal["identity", "cs_percentile", "cs_robust_z"]
    weight: float

    @field_validator("weight")
    @classmethod
    def _check_weight(cls, value: float) -> float:
        if not math.isfinite(value) or value == 0:
            raise ValueError("a weight must be finite and non-zero")
        return value


class RankingCandidate(_Frozen):
    spec_version: Literal[1]
    candidate_id: str = Field(pattern=_SLUG)
    name: str = Field(min_length=1, max_length=200)
    model_family: Literal["linear_weighted_v1"]
    direction: Literal["long", "short"]
    objective: str = Field(min_length=1, max_length=2000)
    terms: list[FeatureTerm] = Field(min_length=1, max_length=MAX_TERMS)
    missing_policy: Literal["exclude"]

    @model_validator(mode="after")
    def _unique_features(self) -> "RankingCandidate":
        features = [t.feature for t in self.terms]
        if len(features) != len(set(features)):
            raise ValueError("each feature may appear once")
        return self

    @property
    def features(self) -> list[str]:
        return [t.feature for t in self.terms]


def canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def candidate_hash(candidate: RankingCandidate) -> str:
    return canonical_hash(candidate.model_dump(mode="json"))


def load_candidate_file(path: Path) -> RankingCandidate:
    """Raises OSError (missing) or pydantic.ValidationError (invalid)."""
    return RankingCandidate.model_validate_json(path.read_text())


def is_finite(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def transform(name: str, values: Sequence[float]) -> tuple[list[float], bool]:
    """One feature's transform over the decision universe. Returns the
    transformed values and whether the feature had no variation."""
    if name == "identity":
        return list(values), False
    if name == "cs_percentile":
        ordered = sorted(values)
        n = len(ordered)
        return [bisect.bisect_right(ordered, v) / n for v in values], ordered[0] == ordered[-1]
    median = statistics.median(values)
    mad = statistics.median(abs(v - median) for v in values)
    if mad == 0:
        return [0.0] * len(values), True
    return [(v - median) / (_MAD_SCALE * mad) for v in values], False


def eligible_indices(features: Mapping[str, Sequence[Any]], required: Sequence[str]) -> list[int]:
    """Positions where every required feature is present and finite."""
    n = len(next(iter(features.values()))) if features else 0
    return [i for i in range(n) if all(is_finite(features[f][i]) for f in required)]


@dataclass(frozen=True)
class TermScore:
    feature: str
    transform: str
    weight: float
    raw: list[float]
    transformed: list[float]
    contribution: list[float]
    degenerate: bool


@dataclass(frozen=True)
class ScoredCrossSection:
    """Scores of one cross-section, in input order, with each term's part —
    `score[i]` is exactly the left-to-right sum of `contribution[i]`."""

    symbols: list[str]
    scores: list[float]
    terms: list[TermScore]

    @property
    def order(self) -> list[int]:
        """Input positions from most to least attractive: score descending,
        ties broken by symbol ascending."""
        return sorted(range(len(self.symbols)), key=lambda i: (-self.scores[i], self.symbols[i]))

    @property
    def degenerate_features(self) -> list[str]:
        return [t.feature for t in self.terms if t.degenerate]


def score_cross_section(
    candidate: RankingCandidate, symbols: list[str], features: Mapping[str, Sequence[float]]
) -> ScoredCrossSection:
    """Score one eligible cross-section. `features` holds, per candidate
    feature, one finite value per symbol — nothing else."""
    scores = [0.0] * len(symbols)
    terms = []
    for term in candidate.terms:
        raw = [float(v) for v in features[term.feature]]
        values, flat = transform(term.transform, raw)
        contribution = [term.weight * v for v in values]
        scores = [s + c for s, c in zip(scores, contribution, strict=True)]
        terms.append(
            TermScore(term.feature, term.transform, term.weight, raw, values, contribution, flat)
        )
    return ScoredCrossSection(list(symbols), scores, terms)
