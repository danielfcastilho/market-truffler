from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime

# Matches app/models/candle.py's `_AMOUNT` — wide enough for a small
# fractional return without losing precision.
_VALUE = Numeric(30, 12)


class SnifferResult(Base):
    """One feature's value for one instrument in one Market Frame.

    Identity is the composite primary key `(frame_time, instrument_id,
    metric)` — the same "one row per fact" shape the prompt itself suggests,
    chosen deliberately over one column per feature: today there is exactly
    one feature (`return_5m`), but this table must not need an
    `ALTER TABLE ADD COLUMN` (or any Sniffer/orchestration change) the day a
    second one is added — only a new row per (frame, instrument) with a new
    `metric` value. The PK also doubles as the idempotency/uniqueness
    enforcement: re-analyzing the same frame is `ON CONFLICT DO UPDATE`, not
    a duplicate row (M5 section 15).

    `value=NULL` is itself meaningful — "Sniffer considered this instrument
    for this metric and it was unavailable" (e.g. the exact historical
    comparison candle didn't exist) — distinct from no row existing at all
    ("this instrument wasn't a member of this frame, so Sniffer never
    considered it" — see M5 section 8).

    Partitioned by RANGE(frame_time), monthly, mirroring `market_candles`/
    `market_frame_members` — same retention mechanism
    (`app.services.partition_manager`), same reason (real per-minute,
    per-instrument scale).
    """

    __tablename__ = "sniffer_results"
    __table_args__ = ({"postgresql_partition_by": "RANGE (frame_time)"},)

    frame_time: Mapped[datetime] = mapped_column(
        ForeignKey("market_frames.frame_time"), primary_key=True, nullable=False
    )
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id"), primary_key=True, nullable=False
    )
    metric: Mapped[str] = mapped_column(String(32), primary_key=True, nullable=False)

    value: Mapped[Decimal | None] = mapped_column(_VALUE)
    calculated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
