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
currently-configured feature's own `required_history`, across both
candle-based and Open-Interest-based features), so a future feature with
a longer lookback automatically raises the READY bar everywhere this is
used, with no change needed in this module.
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


# Ordered least-ready to most-ready, so combining two independent
# watermarks (candle, OI) is just "the lower-ranked of the two states".
_RANK: dict[SymbolReadiness, int] = {
    SymbolReadiness.DISCOVERED: 0,
    SymbolReadiness.BACKFILLING: 1,
    SymbolReadiness.READY: 2,
}


def _watermark_readiness(
    synced_from: datetime | None,
    first_seen_at: datetime | None,
    now: datetime,
    required_warmup: timedelta,
) -> SymbolReadiness:
    """The same three-state classification, generalized over any single
    backward-bootstrap watermark — used for both `history_synced_from`
    (candles) and `oi_synced_from` (Open Interest) below."""
    if synced_from is None or first_seen_at is None:
        return SymbolReadiness.DISCOVERED
    if synced_from <= now - required_warmup:
        return SymbolReadiness.READY
    if synced_from < first_seen_at:
        return SymbolReadiness.BACKFILLING
    return SymbolReadiness.DISCOVERED


def compute_readiness(
    instrument: Instrument, now: datetime, required_warmup: timedelta
) -> SymbolReadiness:
    """A pure classification from `instrument`'s own persisted bootstrap
    watermarks — never triggers any backfill work itself, and never
    re-verifies individual candles/observations for gaps (every feature
    still independently returns `None` for any real remaining gap,
    exactly as before this existed). This only decides whether an
    instrument has plausibly enough calendar history to be worth
    including at all — the same precision level
    `historical_coverage.compute_historical_coverage` already uses for
    its own watermark-based approximation.

    A symbol is READY only once BOTH its candle history
    (`history_synced_from`) and its Open Interest history
    (`oi_synced_from`) independently reach back `required_warmup` — an
    instrument with deep candle history but fresh-discovery OI (or vice
    versa) is only ever as ready as its least-ready data source, since a
    feature built on the lagging one would still be `None`.
    """
    candle_state = _watermark_readiness(
        instrument.history_synced_from, instrument.first_seen_at, now, required_warmup
    )
    oi_state = _watermark_readiness(
        instrument.oi_synced_from, instrument.first_seen_at, now, required_warmup
    )
    return min(candle_state, oi_state, key=lambda state: _RANK[state])


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
