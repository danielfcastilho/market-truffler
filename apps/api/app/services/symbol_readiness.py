"""Per-symbol historical-data readiness: DISCOVERED -> BACKFILLING -> READY.

Sits alongside `historical_coverage.py` (same pure, read-only, watermark-
based computation style — never triggers bootstrap, recovery, or any
Bybit call) but answers a different, per-symbol question: coverage asks
"how much of the whole promised retention window is reconciled, on
average, across the universe" (a Vitals aggregate); this asks "does
*this* instrument specifically have enough already-reconciled history,
right now, to compute the features Sniffer currently requires" — the
concept a future ranking/Score consumer should gate on, so it only ever
surfaces symbols with real data behind them rather than a pile of
still-N/A Sniffs dressed up as a full analysis.

`required_warmup` is never hardcoded here — callers pass
`app.features.engine.REQUIRED_WARMUP` (itself derived from every
currently-configured feature's own `required_history`), so a future
feature with a longer lookback automatically raises the READY bar
everywhere this is used, with no change needed in this module.
"""

from datetime import datetime, timedelta
from enum import StrEnum

from app.models.instrument import Instrument


class SymbolReadiness(StrEnum):
    #: Just discovered — bootstrap hasn't made any backward progress yet.
    DISCOVERED = "discovered"
    #: Bootstrap has started but hasn't yet reached back far enough to
    #: cover `required_warmup`.
    BACKFILLING = "backfilling"
    #: Enough reconciled history exists for every currently-required
    #: feature's lookback to be derivable.
    READY = "ready"


def compute_readiness(
    instrument: Instrument, now: datetime, required_warmup: timedelta
) -> SymbolReadiness:
    """A pure classification from `instrument`'s own persisted bootstrap
    watermark (`history_synced_from`) — never triggers any backfill work
    itself, and never re-verifies individual candles for gaps (RSI/return
    features still independently return `None` for any real remaining
    gap, exactly as before this existed). This only decides whether an
    instrument has plausibly enough calendar history to be worth
    including at all — the same precision level
    `historical_coverage.compute_historical_coverage` already uses for
    its own watermark-based approximation.
    """
    if instrument.history_synced_from is None or instrument.first_seen_at is None:
        return SymbolReadiness.DISCOVERED
    if instrument.history_synced_from <= now - required_warmup:
        return SymbolReadiness.READY
    if instrument.history_synced_from < instrument.first_seen_at:
        return SymbolReadiness.BACKFILLING
    return SymbolReadiness.DISCOVERED


def summarize_readiness(
    instruments: list[Instrument], now: datetime, required_warmup: timedelta
) -> dict[SymbolReadiness, int]:
    """Counts per state across `instruments` (e.g. for Vitals). Always
    reports all three keys, zero-filled, so callers never need a
    defaulting `.get(state, 0)`."""
    counts: dict[SymbolReadiness, int] = dict.fromkeys(SymbolReadiness, 0)
    for instrument in instruments:
        counts[compute_readiness(instrument, now, required_warmup)] += 1
    return counts
