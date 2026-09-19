from datetime import datetime

from sqlalchemy import Boolean, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime


class Instrument(Base):
    """A tradable instrument MARKET has ever seen in the Bybit universe.

    Rows are never deleted when an instrument leaves the active universe
    (`is_active=False`) — its historical candles remain valid and retained;
    only live watching and new bootstrap work stop.

    The three `history_*` columns are MARKET's persisted watermark for this
    instrument's canonical 1m reconciliation, kept intentionally simple:

    - `history_target_start`: how far back MARKET is trying to backfill —
      `now - retention` at first sight, tightened forward if Bybit's REST
      history turns out to start later (a newly listed instrument, or the
      exchange's own data floor).
    - `history_synced_from`: the backward bootstrap frontier — 1m history is
      confirmed reconciled for every minute in
      [history_synced_from, history_synced_through). Decreases toward
      history_target_start as bootstrap progresses; bootstrap is complete
      once it reaches it.
    - `history_synced_through`: the forward/live frontier. Advances by one
      minute at a time as the live WebSocket collector confirms each new
      candle arrives contiguously, or in REST-sized jumps when the
      background reconciler notices it has fallen stale (a WS outage or the
      app having been offline) and fetches the missing range.

    Both frontiers are seeded to the same instant when the instrument is
    first registered, so their union is always one contiguous confirmed
    range — no separate gap-scanning structure is needed.
    """

    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint("exchange", "symbol", name="uq_instruments_exchange_symbol"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    base_coin: Mapped[str] = mapped_column(String(32), nullable=False)
    quote_coin: Mapped[str] = mapped_column(String(32), nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    history_target_start: Mapped[datetime | None] = mapped_column(UTCDateTime())
    history_synced_from: Mapped[datetime | None] = mapped_column(UTCDateTime())
    history_synced_through: Mapped[datetime | None] = mapped_column(UTCDateTime())
