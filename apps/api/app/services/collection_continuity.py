"""MARKET's "Missed frames" metric: how many 5-minute boundaries in the last
24 hours have no Market Frame at all — the visible trace of collection
interruptions (host sleep, Docker stopped, the api down). Live freshness
recovers seconds after a restart; this keeps the gap visible for a day.

Frames are never backfilled (see `app.services.frame_synchronizer`), so a
missing boundary is a permanent hole in the observation record. A
boundary counts as expected only once its frame should have finalized
(`FINALIZE_ALLOWANCE` after it) and never before the first frame ever
recorded, so a fresh install reports nothing missed. Pure and read-only.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.timeframes import BASE_DURATION, floor_to_boundary

WINDOW = timedelta(hours=24)
#: Frames finalize ~30s after their boundary (grace period + build).
FINALIZE_ALLOWANCE = timedelta(minutes=2)


@dataclass(frozen=True)
class MissedFrames:
    expected: int
    missed: int
    last_missed: datetime | None


def missed_frames(
    frame_times: set[datetime], first_frame: datetime | None, now: datetime
) -> MissedFrames:
    if first_frame is None:
        return MissedFrames(0, 0, None)
    last = floor_to_boundary(now - FINALIZE_ALLOWANCE, BASE_DURATION)
    start = max(now - WINDOW, first_frame)
    boundary = floor_to_boundary(start, BASE_DURATION)
    if boundary < start:  # first boundary at or after `start`
        boundary += BASE_DURATION
    expected = missed = 0
    last_missed = None
    while boundary <= last:
        expected += 1
        if boundary not in frame_times:
            missed += 1
            last_missed = boundary
        boundary += BASE_DURATION
    return MissedFrames(expected, missed, last_missed)
