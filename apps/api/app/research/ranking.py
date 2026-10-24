"""OINK CORP ranking research (offline, development only): deterministic
ranking candidates, point-in-time ranking construction, and a versioned
ranking evaluator that compares a candidate with a reference.

CANDIDATES (`research/oink_corp/ranking/candidates/<id>.json`) — model
family `linear_weighted_v1`, a research baseline, not a commitment:

    S_i(t) = sum_j w_j * T_j(f_j(i, t))

- `direction` (long | short) is explicit: a higher score always means a
  more attractive opportunity *in that direction*. Short weights are never
  derived from Long ones.
- Transforms are computed over the decision universe at t only:
  `identity`; `cs_percentile` (fraction of the universe with value <=
  this one — ties share a value, as in experiment specs); `cs_robust_z`
  ((v - median) / (1.4826 * MAD); a zero-MAD feature contributes 0 at that
  timestamp, counted).
- Missing-value policy `exclude`: an instrument lacking any required
  feature (or with a non-finite one) is not in the decision universe.

RANKING at each timestamp t, from features only — labels are never passed
to it: the decision universe E_t (rows with every feature of every model
in the study finite, so candidate and reference rank the same coins), at
least `MIN_UNIVERSE` coins or the timestamp is skipped; scores; order by
score descending, ties broken by symbol ascending.

STUDIES (`research/oink_corp/ranking/studies/<id>.json`) register before
any result: candidate, optional reference, datasets, label (a market-
relative forward return) and top_k. The primary metric is fixed by the
protocol, not chosen per study:

    U(i,t) = R_rel(i,t,h) for Long, -R_rel(i,t,h) for Short
    Q_K(t) = mean U over the top K  -  mean U over E_t (with outcome)
    primary: candidate Q_K, or with a reference the paired
             dQ_K(t) = Q_K^cand(t) - Q_K^ref(t) on identical t and E_t

Outcomes missing after selection are excluded and counted. Diagnostics
(exploratory, never decisive): mean top-K utility, top-minus-bottom-K
separation, Spearman rank correlation of score and utility, per-day
means.

RANKING EVALUATOR v1 reuses Evaluator v1's machinery and thresholds — the
dataset-level day split and purge, one value per timestamp, minimum days,
minimum timestamps, a day-block bootstrap with a deterministic seed, the
interval / day-consistency verdict rules — applied to Q_K(t) or dQ_K(t),
a different estimand than Evaluator v1's spread. Only the development
segment is run here: every result is exploratory.
"""

import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pyarrow as pa
from pydantic import Field, ValidationError, field_validator

from app.ranking.scoring import (
    MIN_CROSS_SECTION,
    FeatureTerm,
    RankingCandidate,
    candidate_hash,
    eligible_indices,
    score_cross_section,
    transform,
)
from app.research import evaluation
from app.research.evaluation import _day_block_interval, _mean, plan_split
from app.research.experiment import (
    DatasetRef,
    ExperimentSpecError,
    _canonical_hash,
    _Frozen,
    load_columns,
)

RANKING_SPEC_VERSION = 1
RANKING_EVALUATOR_VERSION = 1
PRIMARY_METRIC = "top_k_excess_utility"
#: Equal to Evaluator v1's MIN_UNIVERSE_PER_TIMESTAMP (tested).
MIN_UNIVERSE = MIN_CROSS_SECTION
_SLUG = r"^[a-z0-9][a-z0-9-]{0,79}$"

RANKING_DISCLOSURES = (
    "Utilities are market-relative forward returns, not trading P&L: no costs, slippage, "
    "funding, liquidation or position management are modeled.",
    "Weights are manually specified research parameters; nothing was optimized.",
    "Development-segment results only: exploratory, never independent validation.",
    "Coins ranked at the same timestamp move together; timestamps within a day are "
    "dependent (handled by one value per timestamp and day-block resampling only).",
    "Top-ranked coins whose forward outcome is missing (e.g. delistings, data gaps) are "
    "dropped after ranking — a survivorship risk that is counted (top_k_missing_outcome), "
    "not corrected. The universe is each frame's own members, so coins listed later or "
    "delisted earlier are absent by construction.",
)


def universe_disclosure(candidate: RankingCandidate, reference: RankingCandidate | None) -> str:
    """How this study's eligible universe relates to the candidate's live
    ranking (audit F-06): live ranks a candidate over the coins with its own
    features; a paired study ranks both models over the coins with every
    feature of either, so the universes differ exactly when the reference
    needs features the candidate does not."""
    extra = [f for f in (reference.features if reference else []) if f not in candidate.features]
    if not extra:
        return (
            "The evaluated universe matches the candidate's live ranking universe (coins with "
            "every candidate feature)."
        )
    return (
        "The evaluated universe is narrower than the candidate's live ranking universe: it "
        f"also requires the reference's {', '.join(extra)}, so the paired comparison shares "
        "one universe; live ranks every coin with the candidate's own features."
    )


class RankingStudy(_Frozen):
    spec_version: Literal[1]
    study_id: str = Field(pattern=_SLUG)
    name: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=2000)
    candidate: str = Field(pattern=_SLUG)
    reference: str | None = Field(default=None, pattern=_SLUG)
    datasets: list[DatasetRef] = Field(min_length=1)
    label: str
    top_k: int = Field(ge=1, le=200)

    @field_validator("label")
    @classmethod
    def _relative_label(cls, value: str) -> str:
        if not value.startswith("forward_relative_return_"):
            raise ValueError("ranking studies use market-relative forward returns")
        return value


def load_candidate(path: Path) -> RankingCandidate:
    try:
        return RankingCandidate.model_validate_json(path.read_text())
    except ValidationError as exc:
        raise ExperimentSpecError(f"invalid ranking candidate {path}: {exc}") from exc


def load_study(
    path: Path, candidates_dir: Path
) -> tuple[RankingStudy, RankingCandidate, RankingCandidate | None]:
    try:
        study = RankingStudy.model_validate_json(path.read_text())
    except ValidationError as exc:
        raise ExperimentSpecError(f"invalid ranking study {path}: {exc}") from exc
    candidate = load_candidate(candidates_dir / f"{study.candidate}.json")
    reference = (
        load_candidate(candidates_dir / f"{study.reference}.json") if study.reference else None
    )
    for spec, expected in ((candidate, study.candidate), (reference, study.reference)):
        if spec is not None and spec.candidate_id != expected:
            raise ExperimentSpecError(f"{expected}.json declares {spec.candidate_id!r}")
    if reference is not None and reference.direction != candidate.direction:
        raise ExperimentSpecError("candidate and reference must rank the same direction")
    return study, candidate, reference


def study_hash(
    study: RankingStudy, candidate: RankingCandidate, reference: RankingCandidate | None
) -> str:
    return _canonical_hash(
        {
            "study": study.model_dump(mode="json"),
            "candidate": candidate_hash(candidate),
            "reference": candidate_hash(reference) if reference else None,
            "ranking_evaluator_version": RANKING_EVALUATOR_VERSION,
        }
    )


# -- ranking construction (features only) ---------------------------------------------------


def _finite(value: Any) -> bool:
    return isinstance(value, int | float) and math.isfinite(value)


@dataclass(frozen=True)
class Ranking:
    frame_time: datetime
    symbols: list[str]  # ranked, most attractive first
    scores: list[float]
    universe: int
    degenerate_features: list[str]


def rank_timestamp(
    candidate: RankingCandidate,
    frame_time: datetime,
    symbols: list[str],
    features: dict[str, list[float]],
) -> Ranking:
    """Rank the decision universe at one timestamp with the shared scoring
    core (`app.ranking.scoring`, the same code the live rankings use).
    `features` holds, per feature, one finite value per symbol — and
    nothing else: no outcome."""
    scored = score_cross_section(candidate, symbols, features)
    order = scored.order
    return Ranking(
        frame_time,
        [symbols[i] for i in order],
        [scored.scores[i] for i in order],
        len(symbols),
        scored.degenerate_features,
    )


def decision_universes(
    table: pa.Table, features: list[str]
) -> list[tuple[datetime, list[str], dict[str, list[float]], list[int]]]:
    """Per timestamp: symbols with every feature finite (the common decision
    universe), their feature values, and their row indices. Built from
    feature columns only."""
    times = table.column("frame_time").to_pylist()
    symbols = table.column("symbol").to_pylist()
    columns = {f: table.column(f).to_pylist() for f in features}
    out = []
    start = 0
    while start < len(times):
        end = start
        while end < len(times) and times[end] == times[start]:
            end += 1
        rows = [
            start + i
            for i in eligible_indices({f: columns[f][start:end] for f in features}, features)
        ]
        out.append(
            (
                times[start],
                [symbols[i] for i in rows],
                {f: [float(columns[f][i]) for i in rows] for f in features},
                rows,
            )
        )
        start = end
    return out


# -- evaluation ------------------------------------------------------------------------------


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None

    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        result = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            for k in range(i, j + 1):
                result[order[k]] = (i + j) / 2
            i = j + 1
        return result

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    return cov / math.sqrt(vx * vy) if vx and vy else None


@dataclass
class _ModelSeries:
    q: dict[datetime, float]
    top_utility: dict[datetime, float]
    separation: dict[datetime, float]
    spearman: dict[datetime, float]
    top_symbols: Counter[str]
    top_missing_outcome: int
    degenerate: Counter[str]


def _evaluate_model(
    candidate: RankingCandidate,
    universes: list[tuple[datetime, list[str], dict[str, list[float]], list[int]]],
    utility: list[float | None],
    top_k: int,
) -> tuple[_ModelSeries, int]:
    series = _ModelSeries({}, {}, {}, {}, Counter(), 0, Counter())
    too_small = 0
    for frame_time, symbols, features, rows in universes:
        if len(symbols) < MIN_UNIVERSE:
            too_small += 1
            continue
        own = {f: features[f] for f in candidate.features}
        ranking = rank_timestamp(candidate, frame_time, symbols, own)
        u_by_symbol = {s: utility[r] for s, r in zip(symbols, rows, strict=True)}
        ranked_u = [u_by_symbol[s] for s in ranking.symbols]
        labelled = [u for u in ranked_u if u is not None]
        top = [u for u in ranked_u[:top_k] if u is not None]
        bottom = [u for u in ranked_u[-top_k:] if u is not None]
        series.top_missing_outcome += sum(1 for u in ranked_u[:top_k] if u is None)
        for d in ranking.degenerate_features:
            series.degenerate[d] += 1
        if not top or not labelled:
            continue
        universe_mean = math.fsum(labelled) / len(labelled)
        top_mean = math.fsum(top) / len(top)
        series.q[frame_time] = top_mean - universe_mean
        series.top_utility[frame_time] = top_mean
        if bottom:
            series.separation[frame_time] = top_mean - math.fsum(bottom) / len(bottom)
        pairs = [(sc, u) for sc, u in zip(ranking.scores, ranked_u, strict=True) if u is not None]
        rho = _spearman([p[0] for p in pairs], [p[1] for p in pairs])
        if rho is not None:
            series.spearman[frame_time] = rho
        series.top_symbols.update(
            s for s, u in zip(ranking.symbols[:top_k], ranked_u, strict=False) if u is not None
        )
    return series, too_small


def _by_day(values: dict[datetime, float]) -> dict[date, list[float]]:
    days: dict[date, list[float]] = {}
    for t, v in sorted(values.items()):
        days.setdefault(t.date(), []).append(v)
    return days


def _model_summary(series: _ModelSeries) -> dict[str, Any]:
    days = _by_day(series.q)
    return {
        "mean_top_k_excess_utility": _mean(list(series.q.values())),
        "diagnostics": {
            "mean_top_k_utility": _mean(list(series.top_utility.values())),
            "mean_top_bottom_separation": _mean(list(series.separation.values())),
            "mean_spearman": _mean(list(series.spearman.values())),
            "daily_mean_excess_utility": {
                d.isoformat(): math.fsum(v) / len(v) for d, v in sorted(days.items())
            },
        },
        "timestamps": len(series.q),
        "distinct_top_symbols": len(series.top_symbols),
        "top_symbol_share": (
            series.top_symbols.most_common(1)[0][1] / sum(series.top_symbols.values())
            if series.top_symbols
            else None
        ),
        "top_k_missing_outcome": series.top_missing_outcome,
        "degenerate_feature_timestamps": dict(series.degenerate),
    }


def _verdict(
    values: dict[datetime, float], days: set[date], seed: int, concentration: float | None
) -> dict[str, Any]:
    """Evaluator v1's evidence rules applied to a per-timestamp series."""
    day_values = _by_day(values)
    reasons = []
    if len(days) < evaluation.MIN_DAYS_FOR_INFERENCE:
        reasons.append(
            f"{len(days)} qualifying days in segment (< {evaluation.MIN_DAYS_FOR_INFERENCE})"
        )
    if len(values) < evaluation.MIN_SELECTED_TIMESTAMPS:
        reasons.append(
            f"{len(values)} evaluated timestamps (< {evaluation.MIN_SELECTED_TIMESTAMPS})"
        )
    day_means = [math.fsum(v) / len(v) for v in day_values.values()]
    positive = sum(1 for m in day_means if m > 0) / len(day_means) if day_means else None
    negative = sum(1 for m in day_means if m < 0) / len(day_means) if day_means else None
    if len(days) < evaluation.MIN_DAYS_FOR_INFERENCE or not values:
        uncertainty: dict[str, Any] = {
            "suppressed": True,
            "reason": f"fewer than {evaluation.MIN_DAYS_FOR_INFERENCE} qualifying days — a "
            "day-block interval over so few days would be unreliable",
        }
    else:
        low, high = _day_block_interval(day_values, seed)
        uncertainty = {
            "suppressed": False,
            "method": "day-block bootstrap of the timestamp-weighted mean",
            "confidence_level": evaluation.CONFIDENCE_LEVEL,
            "resamples": evaluation.BOOTSTRAP_RESAMPLES,
            "seed": seed,
            "interval": [low, high],
        }
    if reasons:
        verdict = "insufficient_data"
    else:
        low, high = uncertainty["interval"]
        if (
            low > 0
            and (positive or 0) >= evaluation.MIN_CONSISTENT_DAY_FRACTION
            and (concentration or 0) <= evaluation.MAX_TOP_SYMBOL_SHARE
        ):
            verdict = "positive_evidence"
        elif high < 0 and (negative or 0) >= evaluation.MIN_CONSISTENT_DAY_FRACTION:
            verdict = "negative_evidence"
        else:
            verdict = "inconclusive"
            reasons.append("interval, day consistency, or concentration criteria not met")
    return {
        "mean": _mean(list(values.values())),
        "positive_day_fraction": positive,
        "negative_day_fraction": negative,
        "uncertainty": uncertainty,
        "verdict": verdict,
        "verdict_reasons": reasons,
    }


def evaluate_study(
    study: RankingStudy,
    candidate: RankingCandidate,
    reference: RankingCandidate | None,
    data_dir: Path,
    *,
    seed: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Development-segment ranking evaluation. Returns (report, manifests).
    Data comes only through the guarded loader (no access grant: reserved
    validation and holdout periods are refused)."""
    features = list(dict.fromkeys(candidate.features + (reference.features if reference else [])))
    loaded = load_columns(study.datasets, features, study.label, data_dir)
    table = loaded.table
    split = plan_split(table.column("frame_time").to_pylist())
    days = set(split.development_days)
    horizon = timedelta(minutes=loaded.horizon_minutes)
    times = table.column("frame_time").to_pylist()
    keep = [
        i
        for i, t in enumerate(times)
        if t.date() in days
        and (split.purge_boundary is None or t + horizon <= split.purge_boundary)
    ]
    table = table.take(pa.array(keep, type=pa.int64()))
    sign = 1.0 if candidate.direction == "long" else -1.0
    utility = [
        None if v is None or not _finite(v) else sign * float(v)
        for v in table.column(study.label).to_pylist()
    ]
    universes = decision_universes(table, features)
    universe_sizes = [len(s) for _, s, _, _ in universes]

    cand_series, too_small = _evaluate_model(candidate, universes, utility, study.top_k)
    report: dict[str, Any] = {
        "segment": "development",
        "exploratory": True,
        "ranking_evaluator_version": RANKING_EVALUATOR_VERSION,
        "primary_metric": PRIMARY_METRIC,
        "direction": candidate.direction,
        "top_k": study.top_k,
        "label": study.label,
        "horizon_minutes": loaded.horizon_minutes,
        "eligible_universe": "coins with every feature of the candidate and the reference "
        f"finite at that timestamp; at least {MIN_UNIVERSE} or the timestamp is skipped",
        "days": sorted(d.isoformat() for d in days),
        "excluded_days": [d.isoformat() for d in split.excluded_days],
        "coverage": {
            "timestamps": len(universes),
            "timestamps_universe_too_small": too_small,
            "mean_universe": _mean([float(n) for n in universe_sizes]),
            "distinct_days": len(days),
        },
        "candidate": _model_summary(cand_series),
        "disclosures": [*RANKING_DISCLOSURES, universe_disclosure(candidate, reference)],
    }
    if reference is None:
        primary_values = cand_series.q
        concentration = report["candidate"]["top_symbol_share"]
        report["primary"] = {"estimand": "candidate Q_K(t)"}
    else:
        ref_series, _ = _evaluate_model(reference, universes, utility, study.top_k)
        report["reference"] = _model_summary(ref_series)
        matched = sorted(set(cand_series.q) & set(ref_series.q))
        primary_values = {t: cand_series.q[t] - ref_series.q[t] for t in matched}
        concentration = report["candidate"]["top_symbol_share"]
        report["primary"] = {
            "estimand": "paired dQ_K(t) = Q_K(candidate, t) - Q_K(reference, t), "
            "same t and universe",
            "matched_timestamps": len(matched),
            "paired_differences": {
                t.isoformat(): v
                for t, v in zip(matched, [primary_values[t] for t in matched], strict=True)
            },
        }
    report["primary"].update(_verdict(primary_values, days, seed, concentration))
    report["verdict"] = report["primary"]["verdict"]
    report["verdict_reasons"] = report["primary"]["verdict_reasons"]
    return report, loaded.manifests


__all__ = [
    "FeatureTerm",
    "RankingCandidate",
    "eligible_indices",
    "PRIMARY_METRIC",
    "RANKING_EVALUATOR_VERSION",
    "RankingCandidate",
    "RankingStudy",
    "candidate_hash",
    "evaluate_study",
    "load_study",
    "rank_timestamp",
    "study_hash",
    "transform",
]
