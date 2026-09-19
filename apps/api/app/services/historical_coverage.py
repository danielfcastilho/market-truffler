"""MARKET's "Historical coverage" metric.

How much of the promised rolling ~1-year 1m history is actually reconciled
for the CURRENT active universe, right now, according to each active
instrument's persisted watermarks. This is a pure, read-only computation
over already-persisted state — it never triggers bootstrap, recovery, or
any Bybit call, so Vitals reading it never drives MARKET (M3 section 18).

Per-instrument coverage is the fraction of `[target_start, now]` that is
currently confirmed-reconciled, i.e.
`(history_synced_through - history_synced_from) / (now - target_start)`,
where `target_start` is whichever is LATER of:

- the instrument's own discovered floor (`history_target_start` — a newly
  listed instrument, or wherever Bybit's own history happens to run out),
  so a symbol with less than a year of real history can still reach 100%
  without requiring candles that never existed; and
- the current rolling retention cutoff (`now - retention_days`), so a
  reconciled range that has since aged out of retention and been pruned
  correctly stops counting as "currently covered".

Overall "Historical coverage" is the simple mean across all active
instruments — every instrument counts equally, consistent with MARKET
tracking the whole universe without any liquidity/interestingness
weighting.
"""

from datetime import datetime, timedelta

from app.models.instrument import Instrument


def _instrument_coverage(
    instrument: Instrument, now: datetime, retention_days: int
) -> float | None:
    if (
        instrument.history_target_start is None
        or instrument.history_synced_from is None
        or instrument.history_synced_through is None
    ):
        return None

    retention_floor = now - timedelta(days=retention_days)
    target_start = max(instrument.history_target_start, retention_floor)
    synced_from = max(instrument.history_synced_from, target_start)
    synced_through = min(instrument.history_synced_through, now)

    total_span = (now - target_start).total_seconds()
    if total_span <= 0:
        return 1.0

    reconciled_span = max(0.0, (synced_through - synced_from).total_seconds())
    return max(0.0, min(1.0, reconciled_span / total_span))


def compute_historical_coverage(
    instruments: list[Instrument], now: datetime, retention_days: int
) -> float | None:
    """The average reconciliation fraction across `instruments`, in [0, 1].

    None if it can't currently be determined (e.g. no active instruments
    yet) — report that as N/A rather than a fabricated number.
    """
    scores = [
        score
        for score in (
            _instrument_coverage(instrument, now, retention_days) for instrument in instruments
        )
        if score is not None
    ]
    if not scores:
        return None
    return sum(scores) / len(scores)
