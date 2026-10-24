"""OINK CORP incremental comparison (protocol v1): does adding conditions to
a hypothesis change outcomes relative to the simpler hypothesis it refines?

The Evaluator (`app.research.evaluation`, unchanged) compares a selection
with the same-timestamp eligible universe. This asks a narrower question —
the one "does Open Interest add anything beyond price?" needs — for a
*candidate* whose conditions strictly extend a *reference*'s (same
datasets, label and direction). Because conditions are evaluated
independently per feature, every candidate-selected row is also
reference-selected: the candidate is a subset of the reference group.

    spread_t = mean(candidate outcomes at t) - mean(reference outcomes at t)

over timestamps with at least one candidate observation, each timestamp
weighted equally. Only *comparable* rows count on either side: a row must
have every candidate feature and the label (the candidate's eligibility),
so a reference row is never kept merely because a candidate feature is
missing for it. Like the Evaluator's baseline, the reference group includes
the candidate rows; a positive spread means the extra conditions picked the
better part of the reference group.

Everything else is the Evaluator's own: the dataset-level day split, the
purge, the minimum-evidence thresholds, the day-block bootstrap and the
verdict rules, imported rather than copied, so the two can't drift.
Changing any rule here means bumping `COMPARISON_VERSION`.
"""

import hashlib
import math
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from app.research.evaluation import (
    BOOTSTRAP_RESAMPLES,
    CONFIDENCE_LEVEL,
    DISCLOSURES,
    MAX_TOP_SYMBOL_SHARE,
    MIN_CONSISTENT_DAY_FRACTION,
    MIN_DAYS_FOR_INFERENCE,
    MIN_DISTINCT_SELECTED_SYMBOLS,
    MIN_SELECTED_TIMESTAMPS,
    Segment,
    SplitPlan,
    _day_block_interval,
    _mean,
)
from app.research.experiment import ExperimentSpec, ExperimentSpecError, Observations

COMPARISON_VERSION = 1

COMPARISON_DISCLOSURES = (
    "The reference group includes the candidate rows, so the spread understates the "
    "difference between the candidate and the rest of the reference group.",
    "Only rows with every candidate feature present are compared; rows the reference "
    "would select but whose candidate features are missing are excluded from both sides.",
)


def check_nested(candidate: ExperimentSpec, reference: ExperimentSpec) -> None:
    """A comparison is only meaningful when the candidate strictly refines
    the reference on identical data and outcome."""
    if candidate.datasets != reference.datasets:
        raise ExperimentSpecError("candidate and reference must use the same datasets")
    if candidate.label != reference.label:
        raise ExperimentSpecError("candidate and reference must use the same label")
    if candidate.hypothesis.direction != reference.hypothesis.direction:
        raise ExperimentSpecError("candidate and reference must have the same direction")
    extra = list(candidate.hypothesis.conditions)
    for condition in reference.hypothesis.conditions:
        if condition not in extra:
            raise ExperimentSpecError(
                f"reference condition {condition.model_dump()} is not among the candidate's"
            )
        extra.remove(condition)
    if not extra:
        raise ExperimentSpecError("the candidate must add at least one condition")


def comparison_seed(candidate_spec_hash: str, reference_spec_hash: str, segment: Segment) -> int:
    key = f"comparison:{candidate_spec_hash}:{reference_spec_hash}:{segment}"
    return int(hashlib.sha256(key.encode()).hexdigest()[:16], 16)


def evaluate_incremental(
    candidate: Observations,
    reference: Observations,
    *,
    segment: Segment,
    split: SplitPlan,
    horizon_minutes: int,
    seed: int,
) -> dict[str, Any]:
    """The comparison report for one segment. `candidate` and `reference`
    must be the `select` results of a nested pair (`check_nested`) over the
    same table, row for row."""
    if candidate.frame_time != reference.frame_time or candidate.symbol != reference.symbol:
        raise ValueError("candidate and reference observations are not row-aligned")
    for i, chosen in enumerate(candidate.selected):
        if chosen and not reference.selected[i]:
            raise ValueError("candidate selected a row the reference did not — not nested")

    days = set(split.development_days if segment == "development" else split.validation_days)
    if segment == "validation" and not days:
        reason = f"no validation segment: {len(split.qualifying_days)} qualifying days"
        return _without_data(segment, split, reason)
    horizon = timedelta(minutes=horizon_minutes)

    purged_rows = 0
    by_timestamp: dict[datetime, list[int]] = {}
    for i, t in enumerate(candidate.frame_time):
        if t.date() not in days:
            continue
        if (
            segment == "development"
            and split.purge_boundary is not None
            and t + horizon > split.purge_boundary
        ):
            purged_rows += 1
            continue
        by_timestamp.setdefault(t, []).append(i)

    day_spreads: dict[date, list[float]] = {}
    candidate_outcomes: list[float] = []
    reference_outcomes: list[float] = []
    candidate_symbols: Counter[str] = Counter()
    reference_rows_at_selection = 0
    for t, rows in sorted(by_timestamp.items()):
        cand: list[float] = []
        ref: list[float] = []
        for i in rows:
            if not candidate.eligible[i]:  # comparable rows only, on both sides
                continue
            value = candidate.outcome[i]
            assert value is not None
            if reference.selected[i]:
                ref.append(value)
            if candidate.selected[i]:
                cand.append(value)
                candidate_symbols[candidate.symbol[i]] += 1
        if cand:
            reference_rows_at_selection += len(ref)
            candidate_outcomes.extend(cand)
            reference_outcomes.extend(ref)
            spread = math.fsum(cand) / len(cand) - math.fsum(ref) / len(ref)
            day_spreads.setdefault(t.date(), []).append(spread)

    spreads = [s for _, values in sorted(day_spreads.items()) for s in values]
    day_means = [math.fsum(v) / len(v) for _, v in sorted(day_spreads.items())]
    positive = sum(1 for s in day_means if s > 0) / len(day_means) if day_means else None
    negative = sum(1 for s in day_means if s < 0) / len(day_means) if day_means else None
    selected_rows = len(candidate_outcomes)
    top = candidate_symbols.most_common(5)
    top1_share = top[0][1] / selected_rows if top else None

    reasons: list[str] = []
    if len(days) < MIN_DAYS_FOR_INFERENCE:
        reasons.append(f"{len(days)} qualifying days in segment (< {MIN_DAYS_FOR_INFERENCE})")
    if len(spreads) < MIN_SELECTED_TIMESTAMPS:
        reasons.append(f"{len(spreads)} timestamps with selections (< {MIN_SELECTED_TIMESTAMPS})")
    if len(candidate_symbols) < MIN_DISTINCT_SELECTED_SYMBOLS:
        reasons.append(
            f"{len(candidate_symbols)} distinct selected symbols "
            f"(< {MIN_DISTINCT_SELECTED_SYMBOLS})"
        )

    uncertainty: dict[str, Any]
    if len(days) < MIN_DAYS_FOR_INFERENCE or not spreads:
        uncertainty = {
            "suppressed": True,
            "reason": f"fewer than {MIN_DAYS_FOR_INFERENCE} qualifying days — a day-block "
            "interval over so few days would be unreliable",
        }
    else:
        low, high = _day_block_interval(day_spreads, seed)
        uncertainty = {
            "suppressed": False,
            "method": "day-block bootstrap of the timestamp-weighted mean incremental spread",
            "confidence_level": CONFIDENCE_LEVEL,
            "resamples": BOOTSTRAP_RESAMPLES,
            "seed": seed,
            "interval": [low, high],
        }

    if reasons:
        verdict = "insufficient_data"
    else:
        low, high = uncertainty["interval"]
        if (
            low > 0
            and (positive or 0) >= MIN_CONSISTENT_DAY_FRACTION
            and (top1_share or 0) <= MAX_TOP_SYMBOL_SHARE
        ):
            verdict = "positive_evidence"
        elif high < 0 and (negative or 0) >= MIN_CONSISTENT_DAY_FRACTION:
            verdict = "negative_evidence"
        else:
            verdict = "inconclusive"
            reasons.append("interval, day consistency, or concentration criteria not met")

    return {
        "segment": segment,
        "exploratory": segment == "development",
        "comparison_version": COMPARISON_VERSION,
        "days": sorted(d.isoformat() for d in days),
        "excluded_days": [d.isoformat() for d in split.excluded_days],
        "purge_boundary": split.purge_boundary.isoformat() if split.purge_boundary else None,
        "purged_rows": purged_rows,
        "counts": {
            "timestamps": len(by_timestamp),
            "timestamps_with_selection": len(spreads),
            "candidate_rows": selected_rows,
            "reference_rows_at_those_timestamps": reference_rows_at_selection,
            "distinct_days_with_selection": len(day_spreads),
            "distinct_selected_symbols": len(candidate_symbols),
        },
        "concentration": {
            "top_symbols": [{"symbol": s, "rows": n} for s, n in top],
            "top1_share": top1_share,
        },
        "outcomes": {
            "candidate_mean": _mean(candidate_outcomes),
            "reference_mean": _mean(reference_outcomes),
        },
        "spread": {
            "mean": _mean(spreads),
            "positive_day_fraction": positive,
            "negative_day_fraction": negative,
        },
        "uncertainty": uncertainty,
        "verdict": verdict,
        "verdict_reasons": reasons,
        "disclosures": list(DISCLOSURES) + list(COMPARISON_DISCLOSURES),
    }


def _without_data(segment: Segment, split: SplitPlan, reason: str) -> dict[str, Any]:
    return {
        "segment": segment,
        "exploratory": False,
        "comparison_version": COMPARISON_VERSION,
        "days": [],
        "qualifying_days": [d.isoformat() for d in split.qualifying_days],
        "uncertainty": {"suppressed": True, "reason": reason},
        "verdict": "insufficient_data",
        "verdict_reasons": [reason],
        "disclosures": list(DISCLOSURES) + list(COMPARISON_DISCLOSURES),
    }
