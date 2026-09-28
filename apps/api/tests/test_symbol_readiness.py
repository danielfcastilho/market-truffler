from datetime import UTC, datetime, timedelta

from app.models.instrument import Instrument
from app.services.symbol_readiness import SymbolReadiness, compute_readiness, summarize_readiness

REQUIRED_WARMUP = timedelta(hours=60)

# Distinguishes "caller didn't pass oi_synced_from/funding_synced_from"
# (default to mirroring synced_from) from "caller explicitly passed None"
# (a real unset watermark) — a plain `None` default can't tell those apart.
_UNSET = object()


def _instrument(
    *,
    first_seen_at: datetime,
    synced_from: datetime | None,
    oi_synced_from: datetime | None | object = _UNSET,
    funding_synced_from: datetime | None | object = _UNSET,
) -> Instrument:
    """`oi_synced_from`/`funding_synced_from` each default to the same
    value as `synced_from` so tests about candle-watermark transitions
    aren't incidentally held back by an untouched OI or funding watermark
    — see the dedicated interaction tests below for how the three combine."""
    resolved_oi_synced_from = synced_from if oi_synced_from is _UNSET else oi_synced_from
    resolved_funding_synced_from = (
        synced_from if funding_synced_from is _UNSET else funding_synced_from
    )
    return Instrument(
        exchange="bybit",
        symbol="BTCUSDT",
        base_coin="BTC",
        quote_coin="USDT",
        is_active=True,
        first_seen_at=first_seen_at,
        last_seen_at=first_seen_at,
        history_target_start=first_seen_at - timedelta(days=30),
        history_synced_from=synced_from,
        history_synced_through=first_seen_at,
        oi_synced_from=resolved_oi_synced_from,
        funding_synced_from=resolved_funding_synced_from,
    )


def test_discovered_when_no_watermark_exists_yet():
    now = datetime.now(UTC)
    instrument = _instrument(first_seen_at=now, synced_from=None)
    assert compute_readiness(instrument, now, REQUIRED_WARMUP) == SymbolReadiness.DISCOVERED


def test_discovered_immediately_after_reconcile_universe_before_any_bootstrap_progress():
    """`InstrumentRepository.reconcile_universe` seeds a brand-new row with
    `history_synced_from=now` (not None) — this must still read as
    DISCOVERED, not READY or BACKFILLING, until bootstrap has actually
    moved the watermark backward at all."""
    now = datetime.now(UTC)
    instrument = _instrument(first_seen_at=now, synced_from=now)
    assert compute_readiness(instrument, now, REQUIRED_WARMUP) == SymbolReadiness.DISCOVERED


def test_backfilling_when_some_progress_made_but_short_of_the_warmup():
    # `history_synced_from` starts equal to `first_seen_at` at discovery and
    # only ever moves backward (older) as bootstrap progresses — it can
    # never be more recent than `first_seen_at`.
    now = datetime.now(UTC)
    # Bootstrap has walked back 10 hours since discovery — real progress,
    # but short of the 60h warmup RSI's 4h timeframe needs.
    instrument = _instrument(first_seen_at=now, synced_from=now - timedelta(hours=10))
    assert compute_readiness(instrument, now, REQUIRED_WARMUP) == SymbolReadiness.BACKFILLING


def test_ready_when_synced_from_reaches_at_least_the_required_warmup():
    now = datetime.now(UTC)
    instrument = _instrument(first_seen_at=now, synced_from=now - timedelta(hours=61))
    assert compute_readiness(instrument, now, REQUIRED_WARMUP) == SymbolReadiness.READY


def test_ready_at_exactly_the_warmup_boundary():
    now = datetime.now(UTC)
    instrument = _instrument(first_seen_at=now, synced_from=now - REQUIRED_WARMUP)
    assert compute_readiness(instrument, now, REQUIRED_WARMUP) == SymbolReadiness.READY


def test_readiness_never_triggers_backfill_work_itself():
    """A pure function over already-persisted fields only — no I/O, no
    mutation. Calling it repeatedly must be side-effect-free."""
    now = datetime.now(UTC)
    instrument = _instrument(first_seen_at=now, synced_from=now - timedelta(hours=1))
    before = (
        instrument.history_synced_from,
        instrument.history_synced_through,
        instrument.oi_synced_from,
    )
    compute_readiness(instrument, now, REQUIRED_WARMUP)
    compute_readiness(instrument, now, REQUIRED_WARMUP)
    assert (
        instrument.history_synced_from,
        instrument.history_synced_through,
        instrument.oi_synced_from,
    ) == before


def test_summarize_readiness_counts_every_state_and_zero_fills_missing_ones():
    now = datetime.now(UTC)
    discovered = _instrument(first_seen_at=now, synced_from=now)
    backfilling = _instrument(first_seen_at=now, synced_from=now - timedelta(hours=10))
    ready = _instrument(first_seen_at=now, synced_from=now - timedelta(hours=61))

    counts = summarize_readiness([discovered, backfilling, ready], now, REQUIRED_WARMUP)

    assert counts == {
        SymbolReadiness.DISCOVERED: 1,
        SymbolReadiness.BACKFILLING: 1,
        SymbolReadiness.READY: 1,
    }


def test_summarize_readiness_of_an_empty_universe_is_all_zeros_not_missing_keys():
    counts = summarize_readiness([], datetime.now(UTC), REQUIRED_WARMUP)
    assert counts == {
        SymbolReadiness.DISCOVERED: 0,
        SymbolReadiness.BACKFILLING: 0,
        SymbolReadiness.READY: 0,
    }


# -- candle/OI/funding interaction: a symbol is only as ready as its laggard -----


def test_deep_candle_history_does_not_make_a_symbol_ready_while_oi_still_lags():
    now = datetime.now(UTC)
    instrument = _instrument(
        first_seen_at=now,
        synced_from=now - timedelta(hours=61),  # candles: fully warmed up
        oi_synced_from=now - timedelta(hours=10),  # OI: still backfilling
    )
    assert compute_readiness(instrument, now, REQUIRED_WARMUP) == SymbolReadiness.BACKFILLING


def test_deep_oi_history_does_not_make_a_symbol_ready_while_candles_still_lag():
    now = datetime.now(UTC)
    instrument = _instrument(
        first_seen_at=now,
        synced_from=now - timedelta(hours=10),  # candles: still backfilling
        oi_synced_from=now - timedelta(hours=61),  # OI: fully warmed up
    )
    assert compute_readiness(instrument, now, REQUIRED_WARMUP) == SymbolReadiness.BACKFILLING


def test_deep_candle_and_oi_history_does_not_make_a_symbol_ready_while_funding_still_lags():
    now = datetime.now(UTC)
    instrument = _instrument(
        first_seen_at=now,
        synced_from=now - timedelta(hours=61),  # candles: fully warmed up
        oi_synced_from=now - timedelta(hours=61),  # OI: fully warmed up
        funding_synced_from=now - timedelta(hours=10),  # funding: still backfilling
    )
    assert compute_readiness(instrument, now, REQUIRED_WARMUP) == SymbolReadiness.BACKFILLING


def test_deep_funding_history_does_not_make_a_symbol_ready_while_candles_still_lag():
    now = datetime.now(UTC)
    instrument = _instrument(
        first_seen_at=now,
        synced_from=now - timedelta(hours=10),  # candles: still backfilling
        oi_synced_from=now - timedelta(hours=10),  # OI: still backfilling
        funding_synced_from=now - timedelta(hours=61),  # funding: fully warmed up
    )
    assert compute_readiness(instrument, now, REQUIRED_WARMUP) == SymbolReadiness.BACKFILLING


def test_ready_only_once_candle_oi_and_funding_watermarks_all_clear_the_warmup():
    now = datetime.now(UTC)
    instrument = _instrument(
        first_seen_at=now,
        synced_from=now - timedelta(hours=61),
        oi_synced_from=now - timedelta(hours=61),
        funding_synced_from=now - timedelta(hours=61),
    )
    assert compute_readiness(instrument, now, REQUIRED_WARMUP) == SymbolReadiness.READY


def test_oi_bootstrap_not_started_yet_holds_a_symbol_at_discovered_even_with_deep_candles():
    now = datetime.now(UTC)
    instrument = _instrument(
        first_seen_at=now,
        synced_from=now - timedelta(hours=61),
        oi_synced_from=None,
    )
    assert compute_readiness(instrument, now, REQUIRED_WARMUP) == SymbolReadiness.DISCOVERED


def test_funding_bootstrap_not_started_yet_holds_a_symbol_at_discovered_even_with_deep_candles():
    now = datetime.now(UTC)
    instrument = _instrument(
        first_seen_at=now,
        synced_from=now - timedelta(hours=61),
        oi_synced_from=now - timedelta(hours=61),
        funding_synced_from=None,
    )
    assert compute_readiness(instrument, now, REQUIRED_WARMUP) == SymbolReadiness.DISCOVERED
