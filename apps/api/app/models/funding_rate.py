from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime

# Matches app/models/open_interest.py's `_AMOUNT` — wide enough for any
# funding rate (including the rare double-digit-percent extremes some
# instruments have briefly settled at) without losing precision.
_RATE = Numeric(30, 12)


class FundingRateObservation(Base):
    """One actual, settled perpetual funding event for one instrument, at
    Bybit's own per-instrument funding interval (commonly 1h/2h/4h/8h, and
    not necessarily constant over an instrument's lifetime — never
    assumed or hardcoded; see `BybitClient.get_funding_rate_history`).

    Identity is the composite primary key `(instrument_id, funding_time)`
    — the same idempotent-upsert shape `OpenInterestObservation` uses, so
    the same settlement arriving twice (a re-run bootstrap page, a
    retried poll) is a no-op or an exchange-backed correction, never a
    duplicate row. `funding_rate` is stored as Bybit's own raw fraction
    (e.g. `0.0001` for +0.01%), and can legitimately be zero or negative
    — unlike Open Interest, there is no "must be positive" invariant here.

    Deliberately NOT partitioned like `market_candles`/`sniffer_results`,
    for the same reason `OpenInterestObservation` isn't: this only needs
    to cover `app.features.engine.REQUIRED_WARMUP`, not the ~30-day candle
    retention, so the whole table stays small. Retention is a plain
    `DELETE ... WHERE funding_time < cutoff` (see
    `FundingRateRepository.delete_before`), run periodically by
    `app.services.funding_rate_reconciler.FundingRateReconciler`.
    """

    __tablename__ = "funding_rate_observations"

    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id"), primary_key=True, nullable=False
    )
    funding_time: Mapped[datetime] = mapped_column(UTCDateTime(), primary_key=True, nullable=False)

    funding_rate: Mapped[Decimal] = mapped_column(_RATE, nullable=False)
