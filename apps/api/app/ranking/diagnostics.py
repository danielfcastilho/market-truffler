"""Descriptive diagnostics of one ranking snapshot (score distribution,
separation near the top) and of its change from the previous snapshot of
the same model (stability).

Pure functions over already-computed scores: nothing here scores, reads
features or touches the database, so a diagnostic can never disagree with
the ranking it describes. They describe how a model's scores are spread in
one frame — not opportunity quality and not expected profitability (for
`linear_weighted_v1` models over cross-sectional percentiles the score is
relative to the frame by construction).

Conventions: standard deviation is the population deviation of the
eligible universe (it is the whole universe, not a sample); percentiles
interpolate linearly between order statistics (`statistics.quantiles`
method "inclusive"); a statistic that needs more instruments than exist is
None, never approximated.
"""

import math
import statistics
from collections.abc import Sequence
from typing import Any

#: The top of the board the separation and stability diagnostics look at.
TOP_K = 10
PERCENTILES = (10, 25, 75, 90, 99)
HISTOGRAM_BINS = 12
#: "Near the top": within this fraction of the score spread below the top
#: score.
NEAR_TOP_FRACTION = 0.1


def _percentile(ordered: Sequence[float], p: int) -> float:
    if len(ordered) == 1:
        return ordered[0]
    return statistics.quantiles(ordered, n=100, method="inclusive")[p - 1]


def score_distribution(scores: Sequence[float]) -> dict[str, Any]:
    """Distribution of the eligible universe's scores (any order)."""
    n = len(scores)
    if n == 0:
        return {"eligible": 0, "available": False, "reason": "no eligible instruments"}
    ordered = sorted(scores)
    low, high = ordered[0], ordered[-1]
    spread = high - low
    width = spread / HISTOGRAM_BINS
    counts = [0] * HISTOGRAM_BINS
    for s in ordered:
        counts[min(int((s - low) / width), HISTOGRAM_BINS - 1) if width else 0] += 1
    return {
        "eligible": n,
        "available": True,
        "min": low,
        "max": high,
        "mean": math.fsum(ordered) / n,
        "median": statistics.median(ordered),
        "std": statistics.pstdev(ordered),
        "spread": spread,
        "percentiles": {f"p{p}": _percentile(ordered, p) for p in PERCENTILES},
        # Equal-width bins from min to max (all scores in the first bin when
        # every score is equal).
        "histogram": {"low": low, "high": high, "counts": counts},
    }


def separation(ranked_scores: Sequence[float], k: int = TOP_K) -> dict[str, Any]:
    """How far the top of the board stands from the rest. `ranked_scores`
    in rank order (rank 1 first)."""
    n = len(ranked_scores)
    if n == 0:
        return {"available": False, "reason": "no eligible instruments", "k": k}
    top = ranked_scores[0]
    spread = top - ranked_scores[-1]
    rest = ranked_scores[k:]
    return {
        "available": True,
        "k": k,
        "top1_minus_top2": top - ranked_scores[1] if n >= 2 else None,
        "top1_minus_top10": top - ranked_scores[9] if n >= 10 else None,
        # Needs a full top K and at least one instrument outside it.
        "top_k_mean_minus_rest_mean": (
            math.fsum(ranked_scores[:k]) / k - math.fsum(rest) / len(rest) if rest else None
        ),
        "near_top_fraction": NEAR_TOP_FRACTION,
        # Instruments within 10% of the spread of the top score: a crowded
        # top means rank 1 is barely distinguishable from its neighbours.
        "near_top_count": sum(1 for s in ranked_scores if top - s <= NEAR_TOP_FRACTION * spread),
    }


def _spearman(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Pearson correlation of two rank vectors (no ties: ranks are unique)."""
    n = len(a)
    if n < 3:
        return None
    ma, mb = math.fsum(a) / n, math.fsum(b) / n
    cov = math.fsum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True))
    va = math.fsum((x - ma) ** 2 for x in a)
    vb = math.fsum((y - mb) ** 2 for y in b)
    return cov / math.sqrt(va * vb) if va and vb else None


def stability(current: Sequence[str], previous: Sequence[str], k: int = TOP_K) -> dict[str, Any]:
    """Change between two rankings of the same model, each a symbol list in
    rank order. The caller guarantees comparability (same model spec,
    previous frame strictly earlier)."""
    k_now, k_before = current[:k], previous[:k]
    before_rank = {s: r for r, s in enumerate(previous, start=1)}
    now_rank = {s: r for r, s in enumerate(current, start=1)}
    common = [s for s in current if s in before_rank]
    # Re-ranked within the common instruments, so both are 1..m.
    common_set = set(common)
    previous_common = {s: r for r, s in enumerate((s for s in previous if s in common_set), 1)}
    return {
        "available": True,
        "k": k,
        "top_k_overlap": len(set(k_now) & set(k_before)),
        "entered_top_k": [s for s in k_now if s not in set(k_before)],
        "left_top_k": [s for s in k_before if s not in set(k_now)],
        "top_k_rank_changes": [
            {
                "symbol": s,
                "rank": now_rank[s],
                "previous_rank": before_rank.get(s),
                # Positive = moved up the board.
                "change": before_rank[s] - now_rank[s] if s in before_rank else None,
            }
            for s in k_now
        ],
        "common_instruments": len(common),
        # Spearman's rho over the instruments ranked in both snapshots (ranks
        # are unique, so no tie correction).
        "rank_correlation": _spearman(
            [float(r) for r in range(1, len(common) + 1)],
            [float(previous_common[s]) for s in common],
        ),
    }
