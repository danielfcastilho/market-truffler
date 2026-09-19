from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime

# Wide enough for both sub-cent altcoins and BTC-scale turnover without
# losing precision — deliberately generous rather than tuned per-symbol.
_AMOUNT = Numeric(30, 12)

TIMEFRAMES = ("1m", "5m", "15m", "1h")


class Candle(Base):
    """A closed candle for one instrument/timeframe/open_time — never an
    in-progress one.

    Identity is the composite primary key `(instrument_id, timeframe,
    open_time)`, which is also how idempotent upserts are enforced: the same
    candle arriving twice (WebSocket, REST bootstrap, REST recovery, retry)
    is a no-op or a correction, never a duplicate row. `open_time` doubles
    as the native PostgreSQL partition key (monthly range partitions —
    see `app.services.partition_manager`), which is why it must be part of
    every unique key on this table.

    Only 1m rows are exchange-observed; 5m/15m/1h rows are always derived
    locally from a complete set of stored 1m rows (never fetched from Bybit
    directly) — see `app.services.candle_aggregation`.
    """

    __tablename__ = "market_candles"
    __table_args__ = ({"postgresql_partition_by": "RANGE (open_time)"},)

    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id"), primary_key=True, nullable=False
    )
    timeframe: Mapped[str] = mapped_column(String(4), primary_key=True, nullable=False)
    open_time: Mapped[datetime] = mapped_column(UTCDateTime(), primary_key=True, nullable=False)

    close_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    open: Mapped[Decimal] = mapped_column(_AMOUNT, nullable=False)
    high: Mapped[Decimal] = mapped_column(_AMOUNT, nullable=False)
    low: Mapped[Decimal] = mapped_column(_AMOUNT, nullable=False)
    close: Mapped[Decimal] = mapped_column(_AMOUNT, nullable=False)
    volume: Mapped[Decimal] = mapped_column(_AMOUNT, nullable=False)
    turnover: Mapped[Decimal] = mapped_column(_AMOUNT, nullable=False)

    ingested_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )
