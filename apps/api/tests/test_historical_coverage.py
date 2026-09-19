from datetime import UTC, datetime, timedelta

from app.models.instrument import Instrument
from app.services.historical_coverage import compute_historical_coverage

RETENTION_DAYS = 365


def _instrument(
    *,
    target_start: datetime | None,
    synced_from: datetime | None,
    synced_through: datetime | None,
) -> Instrument:
    return Instrument(
        exchange="bybit",
        symbol="BTCUSDT",
        base_coin="BTC",
        quote_coin="USDT",
        is_active=True,
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
        history_target_start=target_start,
        history_synced_from=synced_from,
        history_synced_through=synced_through,
    )


def test_returns_none_when_no_instruments_given():
    assert compute_historical_coverage([], datetime.now(UTC), RETENTION_DAYS) is None


def test_returns_none_for_an_instrument_with_no_watermarks_yet():
    now = datetime.now(UTC)
    instrument = _instrument(target_start=None, synced_from=None, synced_through=None)
    assert compute_historical_coverage([instrument], now, RETENTION_DAYS) is None


def test_freshly_registered_instrument_starts_at_zero_percent():
    now = datetime.now(UTC)
    target_start = now - timedelta(days=RETENTION_DAYS)
    instrument = _instrument(target_start=target_start, synced_from=now, synced_through=now)

    coverage = compute_historical_coverage([instrument], now, RETENTION_DAYS)

    assert coverage == 0.0


def test_fully_reconciled_instrument_is_100_percent():
    now = datetime.now(UTC)
    target_start = now - timedelta(days=RETENTION_DAYS)
    instrument = _instrument(
        target_start=target_start, synced_from=target_start, synced_through=now
    )

    coverage = compute_historical_coverage([instrument], now, RETENTION_DAYS)

    assert coverage == 1.0


def test_partially_reconciled_instrument_is_between_zero_and_one():
    now = datetime.now(UTC)
    target_start = now - timedelta(days=RETENTION_DAYS)
    # Bootstrap has only walked back 30 of the ~365 target days; live/catch-up
    # is fully caught up to now.
    instrument = _instrument(
        target_start=target_start,
        synced_from=now - timedelta(days=30),
        synced_through=now,
    )

    coverage = compute_historical_coverage([instrument], now, RETENTION_DAYS)

    assert coverage is not None
    assert 0.0 < coverage < 1.0
    # ~30/365 of the window is reconciled.
    assert abs(coverage - (30 / RETENTION_DAYS)) < 0.01


def test_newly_listed_instrument_with_less_than_a_year_can_reach_100_percent():
    """A symbol listed 10 days ago has its target_start tightened to its own
    discovered floor (not the full 365-day retention window) — full
    reconciliation of everything that actually exists must read as 100%."""
    now = datetime.now(UTC)
    listed_10_days_ago = now - timedelta(days=10)
    instrument = _instrument(
        target_start=listed_10_days_ago,
        synced_from=listed_10_days_ago,
        synced_through=now,
    )

    coverage = compute_historical_coverage([instrument], now, RETENTION_DAYS)

    assert coverage == 1.0


def test_average_across_multiple_active_instruments():
    now = datetime.now(UTC)
    target_start = now - timedelta(days=RETENTION_DAYS)
    fully_done = _instrument(
        target_start=target_start, synced_from=target_start, synced_through=now
    )
    not_started = _instrument(target_start=target_start, synced_from=now, synced_through=now)

    coverage = compute_historical_coverage([fully_done, not_started], now, RETENTION_DAYS)

    assert coverage == 0.5


def test_reconciled_range_that_has_since_aged_out_of_retention_does_not_count():
    """Old progress beyond the current rolling retention cutoff must not be
    counted as "currently covered" once it's been pruned."""
    now = datetime.now(UTC)
    # This instrument was registered long ago with a target_start far in the
    # past (before retention was this tight / before time moved on), and
    # synced_from never advanced past that stale, now-pruned point.
    stale_target_start = now - timedelta(days=RETENTION_DAYS + 200)
    instrument = _instrument(
        target_start=stale_target_start,
        synced_from=stale_target_start,
        synced_through=now,
    )

    coverage = compute_historical_coverage([instrument], now, RETENTION_DAYS)

    # Effective target_start is clamped to now - retention_days, and
    # synced_from is clamped up to that same floor, so this reads as fully
    # covered for the *current* retention window, not the stale wider one.
    assert coverage == 1.0
