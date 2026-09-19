"""Derives 5m/15m/1h candles locally from stored canonical 1m candles.

Never fetched from Bybit directly (1m is the only exchange-observed
timeframe — see `app.domain.market.ClosedCandle`'s docstring and M3's
invariant B). A derived candle is persisted only once every one of its
constituent 1m candles exists in storage; an incomplete window is silently
left alone; it will complete itself the next time this is called for a
range that includes it (e.g. once a recovered 1m candle fills the hole —
this is what makes "late recovery completes aggregates" work: the caller
just re-runs this over the affected range).

UTC-epoch-aligned bucketing (not relative to application start) is used for
window boundaries, so 5m/15m/1h windows always land on
:00/:05/:10.../:00/:15/:30/:45/hh:00 boundaries — standard exchange-time
alignment.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.repositories.candle_repository import CandleRepository

# (timeframe label, window length in minutes, constituent 1m candles required)
_DERIVED_TIMEFRAMES: tuple[tuple[str, int], ...] = (("5m", 5), ("15m", 15), ("1h", 60))


def _floor_to_boundary(dt: datetime, minutes: int) -> datetime:
    epoch_minutes = int(dt.timestamp() // 60)
    bucket_start_minutes = (epoch_minutes // minutes) * minutes
    return datetime.fromtimestamp(bucket_start_minutes * 60, tz=UTC)


def _ceil_to_boundary(dt: datetime, minutes: int) -> datetime:
    floored = _floor_to_boundary(dt, minutes)
    return floored if floored == dt else floored + timedelta(minutes=minutes)


async def derive_higher_timeframes(
    candle_repo: CandleRepository,
    instrument_id: int,
    touched_start: datetime,
    touched_end: datetime,
) -> dict[str, int]:
    """(Re)derive any 5m/15m/1h windows whose full set of constituent 1m
    candles now exists, anywhere their window overlaps
    `[touched_start, touched_end)`.

    Completeness is always re-checked against storage (not just whatever
    was just ingested), so this is safe to call after a live candle, a
    bootstrap page, a recovered gap, or a correction alike — overlapping
    WebSocket/REST delivery of the same data is harmless (invariant E).

    Returns how many windows were (re)derived per timeframe, for logging/tests.
    """
    derived_counts: dict[str, int] = {}

    for timeframe, minutes in _DERIVED_TIMEFRAMES:
        window_start = _floor_to_boundary(touched_start, minutes)
        window_end = _ceil_to_boundary(touched_end, minutes)

        constituents = await candle_repo.fetch_range(instrument_id, "1m", window_start, window_end)

        buckets: dict[datetime, list] = {}
        for row in constituents:
            bucket_start = _floor_to_boundary(row.open_time, minutes)
            buckets.setdefault(bucket_start, []).append(row)

        rows_to_upsert = []
        for bucket_start, rows in buckets.items():
            if len(rows) != minutes:
                continue  # incomplete window — do not manufacture a partial aggregate

            ordered = sorted(rows, key=lambda r: r.open_time)
            bucket_end = bucket_start + timedelta(minutes=minutes)
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
