"""Boundary-aligned rounds for the OI and funding reconcilers.

Each reconciler polls once per 5-minute boundary, a fixed offset after it,
so the bucket/settlement at T is stored before frame T's decision time
(`app.domain.temporal.decision_time`) — an unaligned 300s loop left about
half the universe one bucket behind at measurement time (audit F-01/F-03).

The wait re-reads the wall clock in short chunks, for the reason
`FrameSynchronizer._sleep_until_next_boundary` documents: a single long
timer runs on the event loop's monotonic clock, which stops while the host
sleeps, and would overshoot after a wake.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from app.domain.timeframes import BASE_DURATION, floor_to_boundary

_WAIT_CHUNK_SECONDS = 5.0


def next_round_at(now: datetime, offset: timedelta) -> datetime:
    """The first `boundary + offset` strictly after `now`."""
    candidate = floor_to_boundary(now, BASE_DURATION) + offset
    while candidate <= now:
        candidate += BASE_DURATION
    return candidate


async def wait_for_round(stop_event: asyncio.Event, offset: timedelta) -> datetime | None:
    """Sleep until the next `boundary + offset` and return that boundary,
    or None if `stop_event` was set first."""
    target = next_round_at(datetime.now(UTC), offset)
    while True:
        remaining = (target - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            return target - offset
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=min(remaining, _WAIT_CHUNK_SECONDS))
            return None
        except TimeoutError:
            continue
