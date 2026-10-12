"""OINK CORP's research-only future-outcome labels: `forward_return_H` and
`forward_relative_return_H`.

This is the ONLY place in the whole codebase allowed to read data with a
timestamp *after* `frame.frame_time` — never a `Feature`, never reachable
from `app.features.engine.FEATURES`, never persisted to `sniffer_results`.
See `app.research.dataset` for how this stays structurally separated from
historical Sniff replay (`app.research.replay`), and `test_forward_returns.py`
for the leakage-regression tests this boundary is checked against.

TEMPORAL SEMANTICS — built on the base timeframe's candle convention (see
`app.domain.timeframes`: a 5m candle opened at `t` covers `[t, t+5m)` and
becomes knowable at `t+5m`):

`frame_time = T` (always a 5-minute boundary) is the decision point
immediately after the 5m candle covering `[T-5m, T)` closed. Its close is
P(T), "the price knowable at T". Symmetrically, P(T+H) is the close of the
5m candle covering `[T+H-5m, T+H)` — the one that closes exactly at T+H —
never the candle opened at T+H itself (that one doesn't close until
T+H+5m: on the wrong side of the boundary). Concretely:

    P(T)   = close of the 5m candle with open_time == T - 5m
             (must be the frame's own `member.m5`)
    P(T+H) = close of the 5m candle with open_time == T + H - 5m
    forward_return_H = P(T+H) / P(T) - 1

e.g. T = 10:05, H = 15m: P(T) is the 10:00 candle's close, P(T+H) the
10:15 candle's close (closing at 10:20).

`member.m5` is the latest *legal* 5m candle, which for an instrument with
a recent gap can be older than `[T-5m, T)`. Using that stale close as P(T)
would silently stretch the label over more than H, so such a member gets
`None` for every horizon — the label is defined only when the start price
is exactly the candle closing at T.

H must be a whole number of 5m candles (`validate_horizons`): a horizon
like 7m has no candle closing exactly at T+H and is rejected outright,
never rounded. Every member of one frame shares the same T, so each
horizon's end candle is one batched `fetch_exact_open_time` query for the
whole frame, never a per-instrument loop.

Missing that exact future candle (a gap, the instrument delisted before
T+H, or T+H simply hasn't happened yet relative to "now") yields `None` —
never approximated, interpolated, or substituted with the nearest
available candle, exactly like every backward-looking feature already
behaves for missing history.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median

from app.domain.frame import MarketFrame
from app.domain.timeframes import BASE_DURATION, BASE_TIMEFRAME, is_aligned, is_base_multiple
from app.models.candle import Candle
from app.repositories.candle_repository import CandleRepository


def validate_horizons(horizons: Mapping[str, timedelta]) -> None:
    """Raise `ValueError` unless every horizon is a positive whole number
    of base (5m) candles — the only horizons with a candle closing exactly
    at T+H."""
    for label, horizon in horizons.items():
        if not is_base_multiple(horizon):
            raise ValueError(
                f"horizon {label!r} ({horizon}) is not a positive multiple of "
                f"{BASE_TIMEFRAME}; it cannot be measured exactly with {BASE_TIMEFRAME} candles"
            )


def start_candle_open_time(frame_time: datetime) -> datetime:
    """The open_time of the 5m candle whose close is P(T)."""
    return frame_time - BASE_DURATION


def end_candle_open_time(frame_time: datetime, horizon: timedelta) -> datetime:
    """The open_time of the 5m candle whose close is P(T+H)."""
    return frame_time + horizon - BASE_DURATION


async def compute_forward_returns(
    frame: MarketFrame,
    candle_repo: CandleRepository,
    horizons: dict[str, timedelta],
) -> dict[int, dict[str, Decimal | None]]:
    """`{instrument_id: {"forward_return_{label}": value | None}}` for
    every member of `frame`, for every `{label: horizon}` pair in
    `horizons`. One batched query per horizon (see module docstring) —
    never one per instrument.

    Raises `ValueError` for a `frame.frame_time` that isn't a 5-minute
    boundary or a horizon that isn't a multiple of 5 minutes."""
    if not is_aligned(frame.frame_time):
        raise ValueError(
            f"frame_time {frame.frame_time.isoformat()} is not a {BASE_TIMEFRAME} boundary"
        )
    validate_horizons(horizons)
    if not frame.members:
        return {}

    instrument_ids = [member.instrument_id for member in frame.members]

    end_candles_by_horizon: dict[str, dict[int, Candle]] = {}
    for label, horizon in horizons.items():
        end_candles_by_horizon[label] = await candle_repo.fetch_exact_open_time(
            BASE_TIMEFRAME, instrument_ids, end_candle_open_time(frame.frame_time, horizon)
        )

    expected_start = start_candle_open_time(frame.frame_time)
    results: dict[int, dict[str, Decimal | None]] = {}
    for member in frame.members:
        start_price = member.m5.close if member.m5.open_time == expected_start else None
        per_horizon: dict[str, Decimal | None] = {}
        for label in horizons:
            end_candle = end_candles_by_horizon[label].get(member.instrument_id)
            if end_candle is None or start_price is None or start_price <= 0:
                per_horizon[f"forward_return_{label}"] = None
            else:
                per_horizon[f"forward_return_{label}"] = (end_candle.close / start_price) - 1
        results[member.instrument_id] = per_horizon

    return results


def compute_forward_relative_returns(
    forward_returns: dict[int, dict[str, Decimal | None]],
    horizon_labels: Sequence[str],
) -> dict[int, dict[str, Decimal | None]]:
    """`{instrument_id: {"forward_relative_return_{label}": value | None}}`
    — each instrument's `forward_return_{label}` minus the **median**
    `forward_return_{label}` across every instrument in the same call that
    has a non-`None` value for that same label.

    Median, not mean: crypto perpetuals routinely have extreme single-
    instrument tail moves (a new/illiquid listing pumping/dumping hundreds
    of percent), and there is no market-cap/liquidity weighting data
    anywhere in this system to make a weighted mean defensible — an
    unweighted mean would be dominated by exactly the outliers a
    cross-sectional "typical market move" reference is supposed to average
    past. Median needs no such weighting assumption. This is a V1
    recommendation, not beyond question — revisit once ranking research
    actually needs a different statistic.

    The reference is computed only over instruments with a real label for
    that same horizon — never treating a missing label as zero, which
    would silently bias the median toward zero as coverage degrades (e.g.
    near the edge of retention).
    """
    results: dict[int, dict[str, Decimal | None]] = {
        instrument_id: {} for instrument_id in forward_returns
    }

    for label in horizon_labels:
        key = f"forward_return_{label}"
        valid_values: list[Decimal] = [
            value
            for per_horizon in forward_returns.values()
            if (value := per_horizon.get(key)) is not None
        ]
        reference = median(valid_values) if valid_values else None

        for instrument_id, per_horizon in forward_returns.items():
            value = per_horizon.get(key)
            relative_key = f"forward_relative_return_{label}"
            if value is None or reference is None:
                results[instrument_id][relative_key] = None
            else:
                results[instrument_id][relative_key] = value - reference

    return results
