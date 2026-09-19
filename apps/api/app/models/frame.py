from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import IntArray, UTCDateTime


class MarketFrame(Base):
    """One unified cross-sectional snapshot of the active market universe,
    produced once per closed UTC minute — see
    `app.services.frame_synchronizer`.

    `frame_time` is the natural primary key (no surrogate id): it is the
    instant immediately after the corresponding minute closed, and every
    candle referenced by this frame's members has `close_time <= frame_time`
    (see `MarketFrameMember`).

    `expected_instrument_ids` is the exact active-universe snapshot taken
    the instant construction begins — never revised retroactively as the
    universe changes later, so a finalized frame's completeness stays a
    stable historical fact (M4 section 6). It is what finalization actually
    iterates, both in the normal same-process path and after a crash and
    restart: a count alone (`expected_instruments`, kept for cheap reads)
    isn't enough to reconstruct the original set once the active universe
    has since changed, so finalization never re-queries "current active
    instruments" — it always reads this column. `expected_instruments` is
    derived from it at write time and the two never diverge.

    A row is inserted with `status="building"` the instant construction
    starts (before the grace-period wait), so a process crash mid-build
    always leaves inspectable state rather than silence (M4 section 19).
    Once `status` is "complete" or "partial" it is terminal: the
    synchronizer never updates a frame again after finalizing it (M4
    section 15/16 — a finalized frame is an immutable statement of what was
    actually available at T).
    """

    __tablename__ = "market_frames"

    frame_time: Mapped[datetime] = mapped_column(UTCDateTime(), primary_key=True, nullable=False)
    expected_instrument_ids: Mapped[list[int]] = mapped_column(IntArray(), nullable=False)
    expected_instruments: Mapped[int] = mapped_column(Integer, nullable=False)
    available_instruments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class MarketFrameMember(Base):
    """One instrument's fully-available synchronized context for one frame.

    A row exists only for instruments where all four configured timeframes
    had a legal candle at finalization time — an instrument missing even
    one timeframe simply has no member row here (frame-level
    `available_instruments` counts these rows; the gap versus
    `expected_instruments` is what made the frame PARTIAL).

    Each timeframe is referenced by its `open_time` alone — the identifying
    half of `market_candles`' own composite key
    `(instrument_id, timeframe, open_time)`, with `timeframe` implied by
    the column and `instrument_id` implied by this row. The full OHLCV
    context is fetched by joining back to `market_candles` on demand
    (`app.repositories.frame_repository`), never duplicated here — keeping
    this table's rows compact at the ~405M/year scale one row per
    instrument per minute implies. A genuine foreign key per timeframe
    would need a redundant constant `timeframe` column on each of the four
    (FK target columns can't be literals); not worth it for four fixed
    values, so these are plain indexed-by-PK datetime columns instead,
    documented here rather than enforced by the schema.

    Partitioned by RANGE(frame_time), monthly, mirroring `market_candles` —
    same retention mechanism (`app.services.partition_manager`), same
    reason (real scale, not premature optimization).
    """

    __tablename__ = "market_frame_members"
    __table_args__ = ({"postgresql_partition_by": "RANGE (frame_time)"},)

    frame_time: Mapped[datetime] = mapped_column(
        ForeignKey("market_frames.frame_time"), primary_key=True, nullable=False
    )
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id"), primary_key=True, nullable=False
    )

    open_time_1m: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    open_time_5m: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    open_time_15m: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    open_time_1h: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
