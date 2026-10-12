"""OINK CORP's independent Evaluator (protocol v1).

Every evaluation rule lives here as a versioned constant — chronological
splitting, purging, the baseline, minimum evidence, uncertainty, verdict
thresholds. None can be set by an experiment spec; this module never sees
a spec's conditions, only the resulting `Observations`.

BASELINE — same-timestamp eligible universe. For each timestamp t with at
least one selected observation that has an outcome:

    spread_t = mean(selected directional outcomes at t)
             - mean(eligible directional outcomes at t)

The selected observations are part of that universe. The headline is the
mean of spread_t over timestamps, each timestamp weighted equally — one
number per timestamp, so hundreds of co-moving coins never count as
hundreds of independent observations.

SPLIT — whole UTC calendar days, decided from dataset-level availability
only (never from which rows a hypothesis selects), so every experiment on
the same datasets gets the same segments. A day qualifies when it has at
least `MIN_TIMESTAMPS_PER_QUALIFYING_DAY` timestamps with at least
`MIN_UNIVERSE_PER_TIMESTAMP` dataset rows. With N qualifying days and
N >= `MIN_DAYS_FOR_SPLIT`, the latest max(3, N // 3) form the validation
segment (N = 6 -> 3 development / 3 validation); otherwise there is no
validation segment. Development observations whose label window ends after
the first validation day begins (t + H > boundary) are purged.

THRESHOLDS ARE PROVISIONAL ENGINEERING SAFEGUARDS, not calibrated
statistics: they keep thin, clustered samples from producing confident
verdicts. Changing any of them means bumping `EVALUATOR_VERSION`.
"""

import hashlib
import math
import random
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from app.research.experiment import Observations

EVALUATOR_VERSION = 1

MIN_UNIVERSE_PER_TIMESTAMP = 50
MIN_TIMESTAMPS_PER_QUALIFYING_DAY = 12
MIN_DAYS_FOR_SPLIT = 6
MIN_VALIDATION_DAYS = 3
MIN_DAYS_FOR_INFERENCE = 10
MIN_SELECTED_TIMESTAMPS = 200
MIN_DISTINCT_SELECTED_SYMBOLS = 30
MAX_TOP_SYMBOL_SHARE = 0.20
MIN_CONSISTENT_DAY_FRACTION = 0.60
BOOTSTRAP_RESAMPLES = 2000
CONFIDENCE_LEVEL = 0.90

Segment = Literal["development", "validation"]
Verdict = Literal["insufficient_data", "positive_evidence", "negative_evidence", "inconclusive"]

DISCLOSURES = (
    "Verdicts describe evidence in this sample only; none is proof of a profitable "
    "trading strategy.",
    "No trading costs, slippage, funding payments, capacity, or execution are modeled.",
    "Forward-return windows of consecutive timestamps overlap whenever the sampling "
    "interval is shorter than the horizon; observations within a day are not independent.",
    "Coins move together: cross-sectional dependence is handled only by aggregating to "
    "one spread per timestamp.",
    "Missing forward outcomes (e.g. delistings, data gaps) are excluded after selection — "
    "a survivorship risk that is counted, not corrected.",
    "The sample covers few days and few market regimes.",
    "Evaluation thresholds are provisional engineering safeguards, not calibrated "
    "statistical guarantees.",
    "No validation result is independent once the validation days have been used by other "
    "experiments; see validation_reuse.",
)


@dataclass(frozen=True)
class SplitPlan:
    qualifying_days: list[date]
    excluded_days: list[date]
    development_days: list[date]
    validation_days: list[date]
    #: Start of the first validation day; None without a validation segment.
    purge_boundary: datetime | None


def plan_split(frame_times: list[datetime]) -> SplitPlan:
    """Dataset-level day split — `frame_times` is every dataset row's
    timestamp (one entry per row), independent of any hypothesis."""
    rows_per_timestamp = Counter(frame_times)
    good_timestamps_per_day: Counter[date] = Counter()
    all_days: set[date] = set()
    for timestamp, rows in rows_per_timestamp.items():
        all_days.add(timestamp.date())
        if rows >= MIN_UNIVERSE_PER_TIMESTAMP:
            good_timestamps_per_day[timestamp.date()] += 1
    qualifying = sorted(
        d for d, n in good_timestamps_per_day.items() if n >= MIN_TIMESTAMPS_PER_QUALIFYING_DAY
    )
    excluded = sorted(all_days - set(qualifying))

    if len(qualifying) < MIN_DAYS_FOR_SPLIT:
        return SplitPlan(qualifying, excluded, list(qualifying), [], None)
    n_validation = max(MIN_VALIDATION_DAYS, len(qualifying) // 3)
    development, validation = qualifying[:-n_validation], qualifying[-n_validation:]
    boundary = datetime.combine(validation[0], datetime.min.time(), tzinfo=UTC)
    return SplitPlan(qualifying, excluded, development, validation, boundary)


def bootstrap_seed(spec_hash: str, segment: Segment) -> int:
    return int(hashlib.sha256(f"{spec_hash}:{segment}".encode()).hexdigest()[:16], 16)


def _mean(values: list[float]) -> float | None:
    return math.fsum(values) / len(values) if values else None


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _day_block_interval(day_spreads: dict[date, list[float]], seed: int) -> tuple[float, float]:
    """Percentile interval for the timestamp-weighted mean spread,
    resampling whole days (blocks) with replacement — keeps each day's
    correlated, overlapping-horizon timestamps together."""
    blocks = [(math.fsum(v), len(v)) for _, v in sorted(day_spreads.items())]
    rng = random.Random(seed)
    means = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        sample = [blocks[rng.randrange(len(blocks))] for _ in blocks]
        means.append(sum(s for s, _ in sample) / sum(n for _, n in sample))
    means.sort()
    tail = (1 - CONFIDENCE_LEVEL) / 2
    low = means[int(math.floor(tail * (BOOTSTRAP_RESAMPLES - 1)))]
    high = means[int(math.ceil((1 - tail) * (BOOTSTRAP_RESAMPLES - 1)))]
    return low, high


def evaluate(
    observations: Observations,
    *,
    segment: Segment,
    split: SplitPlan,
    horizon_minutes: int,
    sample_interval_minutes: int,
    seed: int,
    validation_reuse: dict[str, Any],
) -> dict[str, Any]:
    """The evaluation report for one segment, including its verdict."""
    days = set(split.development_days if segment == "development" else split.validation_days)
    horizon = timedelta(minutes=horizon_minutes)

    if segment == "validation" and not days:
        return _report_without_data(
            segment,
            split,
            validation_reuse,
            f"no validation segment: {len(split.qualifying_days)} qualifying days, "
            f"at least {MIN_DAYS_FOR_SPLIT} required",
        )

    purged_rows = 0
    by_timestamp: dict[datetime, list[int]] = {}
    for i, t in enumerate(observations.frame_time):
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

    selected_at_decision = selected_with_outcome = eligible_rows = universe_rows = 0
    selected_outcomes: list[float] = []
    eligible_outcomes: list[float] = []
    selected_symbols: Counter[str] = Counter()
    universe_symbols: set[str] = set()
    day_spreads: dict[date, list[float]] = {}
    day_timestamps: Counter[date] = Counter()
    for t, rows in sorted(by_timestamp.items()):
        day_timestamps[t.date()] += 1
        universe_rows += len(rows)
        sel: list[float] = []
        elig: list[float] = []
        for i in rows:
            universe_symbols.add(observations.symbol[i])
            value = observations.outcome[i]
            if observations.eligible[i]:
                assert value is not None
                elig.append(value)
            if observations.selected[i]:
                selected_at_decision += 1
                if observations.eligible[i]:
                    assert value is not None
                    sel.append(value)
                    selected_symbols[observations.symbol[i]] += 1
        eligible_rows += len(elig)
        selected_with_outcome += len(sel)
        selected_outcomes.extend(sel)
        eligible_outcomes.extend(elig)
        if sel:
            spread = math.fsum(sel) / len(sel) - math.fsum(elig) / len(elig)
            day_spreads.setdefault(t.date(), []).append(spread)

    all_spreads = [s for _, values in sorted(day_spreads.items()) for s in values]
    mean_spread = _mean(all_spreads)
    daily = [
        {
            "day": d.isoformat(),
            "timestamps": day_timestamps[d],
            "timestamps_with_selection": len(day_spreads.get(d, [])),
            "mean_spread": _mean(day_spreads.get(d, [])),
        }
        for d in sorted(days)
    ]
    days_with_spread = [math.fsum(v) / len(v) for _, v in sorted(day_spreads.items())]
    positive_day_fraction = (
        sum(1 for s in days_with_spread if s > 0) / len(days_with_spread)
        if days_with_spread
        else None
    )
    negative_day_fraction = (
        sum(1 for s in days_with_spread if s < 0) / len(days_with_spread)
        if days_with_spread
        else None
    )
    top = selected_symbols.most_common(5)
    top1_share = top[0][1] / selected_with_outcome if top else None
    top5_share = sum(n for _, n in top) / selected_with_outcome if top else None

    reasons: list[str] = []
    if len(days) < MIN_DAYS_FOR_INFERENCE:
        reasons.append(f"{len(days)} qualifying days in segment (< {MIN_DAYS_FOR_INFERENCE})")
    if len(all_spreads) < MIN_SELECTED_TIMESTAMPS:
        reasons.append(
            f"{len(all_spreads)} timestamps with selections (< {MIN_SELECTED_TIMESTAMPS})"
        )
    if len(selected_symbols) < MIN_DISTINCT_SELECTED_SYMBOLS:
        reasons.append(
            f"{len(selected_symbols)} distinct selected symbols (< {MIN_DISTINCT_SELECTED_SYMBOLS})"
        )

    uncertainty: dict[str, Any]
    verdict: Verdict
    if len(days) < MIN_DAYS_FOR_INFERENCE or not all_spreads:
        uncertainty = {
            "suppressed": True,
            "reason": f"fewer than {MIN_DAYS_FOR_INFERENCE} qualifying days — a day-block "
            "interval over so few days would be unreliable",
        }
    else:
        low, high = _day_block_interval(day_spreads, seed)
        uncertainty = {
            "suppressed": False,
            "method": "day-block bootstrap of the timestamp-weighted mean spread",
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
            and (positive_day_fraction or 0) >= MIN_CONSISTENT_DAY_FRACTION
            and (top1_share or 0) <= MAX_TOP_SYMBOL_SHARE
        ):
            verdict = "positive_evidence"
        elif high < 0 and (negative_day_fraction or 0) >= MIN_CONSISTENT_DAY_FRACTION:
            verdict = "negative_evidence"
        else:
            verdict = "inconclusive"
            reasons.append("interval, day consistency, or concentration criteria not met")

    timestamps = len(by_timestamp)
    return {
        "segment": segment,
        "exploratory": segment == "development",
        "days": sorted(d.isoformat() for d in days),
        "excluded_days": [d.isoformat() for d in split.excluded_days],
        "purge_boundary": split.purge_boundary.isoformat() if split.purge_boundary else None,
        "purged_rows": purged_rows,
        "counts": {
            "universe_rows": universe_rows,
            "eligible_universe_rows": eligible_rows,
            "universe_rows_excluded": universe_rows - eligible_rows,
            "selected_at_decision": selected_at_decision,
            "selected_with_outcome": selected_with_outcome,
            "selected_missing_outcome": selected_at_decision - selected_with_outcome,
            "timestamps": timestamps,
            "timestamps_with_selection": len(all_spreads),
            "timestamps_without_selection": timestamps - len(all_spreads),
            "distinct_days": len(days),
            "distinct_selected_symbols": len(selected_symbols),
            "distinct_universe_symbols": len(universe_symbols),
            "effective_independent_windows": timestamps * sample_interval_minutes / horizon_minutes,
        },
        "concentration": {
            "top_symbols": [{"symbol": s, "rows": n} for s, n in top],
            "top1_share": top1_share,
            "top5_share": top5_share,
        },
        "outcomes": {
            "selected_mean": _mean(selected_outcomes),
            "selected_median": _median(selected_outcomes),
            "baseline_mean": _mean(eligible_outcomes),
            "baseline_median": _median(eligible_outcomes),
            "baseline_note": "same-timestamp eligible universe, which includes the selected rows",
        },
        "spread": {
            "mean": mean_spread,
            "positive_day_fraction": positive_day_fraction,
            "negative_day_fraction": negative_day_fraction,
            "daily": daily,
        },
        "coverage": {
            "eligible_fraction": eligible_rows / universe_rows if universe_rows else None,
        },
        "uncertainty": uncertainty,
        "validation_reuse": validation_reuse,
        "verdict": verdict,
        "verdict_reasons": reasons,
        "disclosures": list(DISCLOSURES),
    }


def _report_without_data(
    segment: Segment, split: SplitPlan, validation_reuse: dict[str, Any], reason: str
) -> dict[str, Any]:
    return {
        "segment": segment,
        "exploratory": False,
        "days": [],
        "excluded_days": [d.isoformat() for d in split.excluded_days],
        "qualifying_days": [d.isoformat() for d in split.qualifying_days],
        "uncertainty": {"suppressed": True, "reason": reason},
        "validation_reuse": validation_reuse,
        "verdict": "insufficient_data",
        "verdict_reasons": [reason],
        "disclosures": list(DISCLOSURES),
    }
