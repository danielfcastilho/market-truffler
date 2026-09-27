from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime

# Matches app/models/candle.py's `_AMOUNT` — wide enough for any
# instrument's open interest without losing precision.
_AMOUNT = Numeric(30, 12)


class OpenInterestObservation(Base):
    """One point-in-time open interest reading for one instrument, at
    Bybit's own 5-minute bucket granularity.

    Identity is the composite primary key `(instrument_id, observed_at)` —
    the same idempotent-upsert shape `market_candles` uses, so the same
    observation arriving twice (a re-run bootstrap page, a retried poll)
    is a no-op or an exchange-backed correction, never a duplicate row.

    Deliberately NOT partitioned like `market_candles`/`sniffer_results`:
    OI only needs to cover `app.features.engine.REQUIRED_WARMUP` (a few
    days at most, not the ~30-day candle retention), and at one row per
    instrument per 5 minutes the whole table stays orders of magnitude
    smaller than the partitioned tables that scheme exists for. Retention
    is a plain `DELETE ... WHERE observed_at < cutoff` (see
    `OpenInterestRepository.delete_before`), run periodically by
    `app.services.open_interest_reconciler.OpenInterestReconciler`.
    """

    __tablename__ = "open_interest_observations"

    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id"), primary_key=True, nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), primary_key=True, nullable=False)

    open_interest: Mapped[Decimal] = mapped_column(_AMOUNT, nullable=False)
