from datetime import UTC, datetime, timedelta

from app.services.collection_continuity import missed_frames

NOW = datetime(2026, 10, 24, 16, 1, 30, tzinfo=UTC)  # 15:55 is the last finalized boundary
STEP = timedelta(minutes=5)


def _grid(start: datetime, end: datetime) -> set[datetime]:
    times, t = set(), start
    while t <= end:
        times.add(t)
        t += STEP
    return times


def test_nothing_recorded_yet_is_nothing_missed():
    assert missed_frames(set(), None, NOW).expected == 0


def test_an_unbroken_day_misses_nothing():
    first = NOW - timedelta(days=3)
    frames = _grid(
        datetime(2026, 10, 23, 16, 5, tzinfo=UTC), datetime(2026, 10, 24, 15, 55, tzinfo=UTC)
    )
    result = missed_frames(frames, first, NOW)
    assert (result.expected, result.missed, result.last_missed) == (287, 0, None)  # 16:05 .. 15:55


def test_a_sleep_gap_is_counted_and_dated():
    frames = _grid(
        datetime(2026, 10, 23, 16, 5, tzinfo=UTC), datetime(2026, 10, 24, 15, 55, tzinfo=UTC)
    )
    gap = _grid(datetime(2026, 10, 24, 3, 0, tzinfo=UTC), datetime(2026, 10, 24, 4, 55, tzinfo=UTC))
    result = missed_frames(frames - gap, NOW - timedelta(days=3), NOW)
    assert result.missed == 24
    assert result.last_missed == datetime(2026, 10, 24, 4, 55, tzinfo=UTC)


def test_the_boundary_still_finalizing_is_not_yet_expected():
    now = datetime(2026, 10, 24, 16, 0, 40, tzinfo=UTC)  # 16:00 may still be building
    result = missed_frames(
        {datetime(2026, 10, 24, 15, 55, tzinfo=UTC)},
        datetime(2026, 10, 24, 15, 55, tzinfo=UTC),
        now,
    )
    assert (result.expected, result.missed) == (1, 0)


def test_nothing_before_the_first_frame_ever_counts():
    first = datetime(2026, 10, 24, 15, 12, 30, tzinfo=UTC)  # first boundary after: 15:15
    frames = _grid(
        datetime(2026, 10, 24, 15, 15, tzinfo=UTC), datetime(2026, 10, 24, 15, 55, tzinfo=UTC)
    )
    result = missed_frames(frames, first, NOW)
    assert (result.expected, result.missed) == (9, 0)
