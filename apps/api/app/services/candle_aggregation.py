"""Derives 15m/1h/4h/24h candles locally from stored base (5m) candles.

Never fetched from Bybit directly (5m is the only exchange-observed
timeframe — see `app.domain.timeframes`). A derived candle is persisted
only once every one of its constituent 5m candles exists in storage; an
incomplete window is silently left alone; it will complete itself the next
time this is called for a range that includes it (e.g. once a recovered 5m
candle fills the hole — this is what makes "late recovery completes
aggregates" work: the caller just re-runs this over the affected range).

UTC-epoch-aligned bucketing (not relative to application start) is used for
window boundaries, so 15m/1h/4h/24h windows always land on
:00/:15/:30/:45 / hh:00 / 00:00-04:00-08:00-12:00-16:00-20:00 / 00:00 UTC
boundaries — standard exchange-time alignment. Every derived timeframe
(including 4h and 24h) is derived directly from 5m (respectively 3, 12, 48
and 288 constituent candles), the same one-level scheme — never chained
through an intermediate timeframe — so this module has no special-casing
per timeframe beyond `app.domain.timeframes.TIMEFRAMES`.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from app.domain.timeframes import BASE_DURATION, BASE_TIMEFRAME, TIMEFRAMES, floor_to_boundary
from app.repositories.candle_repository import CandleRepository

# (timeframe label, window length) for every timeframe derived from the base.
_DERIVED_TIMEFRAMES: tuple[tuple[str, timedelta], ...] = tuple(
    (label, duration) for label, duration in TIMEFRAMES.items() if label != BASE_TIMEFRAME
)


def _ceil_to_boundary(dt: datetime, duration: timedelta) -> datetime:
    floored = floor_to_boundary(dt, duration)
    return floored if floored == dt else floored + duration


async def derive_higher_timeframes(
    candle_repo: CandleRepository,
    instrument_id: int,
    touched_start: datetime,
    touched_end: datetime,
) -> dict[str, int]:
    """(Re)derive any 15m/1h/4h/24h windows whose full set of constituent
    5m candles now exists, anywhere their window overlaps
    `[touched_start, touched_end)`.

    Completeness is always re-checked against storage (not just whatever
    was just ingested), so this is safe to call after a live candle, a
    bootstrap page, a recovered gap, or a correction alike — overlapping
    WebSocket/REST delivery of the same data is harmless (invariant E).

    Returns how many windows were (re)derived per timeframe, for logging/tests.
    """
    derived_counts: dict[str, int] = {}

    for timeframe, duration in _DERIVED_TIMEFRAMES:
        constituents_required = duration // BASE_DURATION
        window_start = floor_to_boundary(touched_start, duration)
        window_end = _ceil_to_boundary(touched_end, duration)

        constituents = await candle_repo.fetch_range(
            instrument_id, BASE_TIMEFRAME, window_start, window_end
        )

        buckets: dict[datetime, list] = {}
        for row in constituents:
            bucket_start = floor_to_boundary(row.open_time, duration)
            buckets.setdefault(bucket_start, []).append(row)

        rows_to_upsert = []
        for bucket_start, rows in buckets.items():
            if len(rows) != constituents_required:
                continue  # incomplete window — do not manufacture a partial aggregate

            ordered = sorted(rows, key=lambda r: r.open_time)
            bucket_end = bucket_start + duration
            rows_to_upsert.append(
                {
                    "instrument_id": instrument_id,
                    "timeframe": timeframe,
                    "open_time": bucket_start,
                    "close_time": bucket_end - timedelta(microseconds=1),
                    "open": ordered[0].open,
                    "high": max(r.high for r in ordered),
                    "low": min(r.low for r in ordered),
                    "close": ordered[-1].close,
                    "volume": sum((r.volume for r in ordered), Decimal(0)),
                    "turnover": sum((r.turnover for r in ordered), Decimal(0)),
                }
            )

        if rows_to_upsert:
            await candle_repo.upsert_many(rows_to_upsert)
        derived_counts[timeframe] = len(rows_to_upsert)

    return derived_counts
