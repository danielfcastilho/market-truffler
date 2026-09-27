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

    - `history_target_start`: the initial discovery target (`now - retention`),
      not rewritten when policy changes or REST reaches its data floor.
    - `history_synced_from`: the backward reconciliation frontier, including
      ranges checked but empty at the exchange. Bootstrap stops at the later
      of the initial target and today's rolling retention floor.
    - `history_synced_through`: the forward reconciliation frontier, advanced
      by the reconciler (which first checks for already-ingested live data).
      Catch-up skips work older than the current retention floor after outages.

    These record reconciliation work, not the oldest/newest physical row.
    Retention does not rewrite them; coverage intersects them with the current
    window. Expired intervals can no longer be assumed physically present.

    `oi_synced_from` is the equivalent backward-bootstrap watermark for
    Open Interest (`open_interest_observations`), owned by
    `app.services.open_interest_reconciler.OpenInterestReconciler` — kept
    as its own column rather than reusing `history_synced_from` because OI
    and candles are fetched from different Bybit endpoints on independent
    schedules, and a symbol can legitimately have deep candle history
    while OI backfill is still catching up (or vice versa). There is no
    `oi_synced_through`: forward catch-up resumes from
    `MAX(open_interest_observations.observed_at)` for the instrument
    directly rather than a redundant persisted frontier — OI's bounded,
    short (`REQUIRED_WARMUP`-scale, not the ~30-day candle retention)
    window makes that cheap to compute on demand.
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
    oi_synced_from: Mapped[datetime | None] = mapped_column(UTCDateTime())
