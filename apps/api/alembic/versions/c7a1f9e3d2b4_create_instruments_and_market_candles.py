"""create instruments and market_candles tables

Revision ID: c7a1f9e3d2b4
Revises: b1bb2b530a3b
Create Date: 2026-09-19 18:00:00.000000

"""
from datetime import UTC, datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7a1f9e3d2b4'
down_revision: Union[str, None] = 'b1bb2b530a3b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Wide enough for both sub-cent altcoins and BTC-scale turnover — matches
# app/models/candle.py's `_AMOUNT`.
_AMOUNT = sa.Numeric(30, 12)


def _month_bounds(offset_months: int) -> tuple[datetime, datetime, str]:
    """Return (start, end, partition_name_suffix) for the calendar month
    `offset_months` away from the current one, e.g. offset=0 is this month,
    offset=-1 is last month, offset=1 is next month.
    """
    now = datetime.now(UTC)
    year = now.year + (now.month - 1 + offset_months) // 12
    month = (now.month - 1 + offset_months) % 12 + 1
    start = datetime(year, month, 1, tzinfo=UTC)
    end_year = year + (month // 12)
    end_month = month % 12 + 1
    end = datetime(end_year, end_month, 1, tzinfo=UTC)
    return start, end, f"{year:04d}_{month:02d}"


def upgrade() -> None:
    op.create_table(
        'instruments',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('exchange', sa.String(length=32), nullable=False),
        sa.Column('symbol', sa.String(length=32), nullable=False),
        sa.Column('base_coin', sa.String(length=32), nullable=False),
        sa.Column('quote_coin', sa.String(length=32), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('history_target_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('history_synced_from', sa.DateTime(timezone=True), nullable=True),
        sa.Column('history_synced_through', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('exchange', 'symbol', name='uq_instruments_exchange_symbol'),
    )
    op.create_index(op.f('ix_instruments_symbol'), 'instruments', ['symbol'])

    # The parent partitioned table. It holds no rows of its own — every row
    # lives in one of the monthly partitions created below (initially) or by
    # `app.services.partition_manager` (on an ongoing basis, as time moves
    # forward and old partitions age out of the retention window).
    op.create_table(
        'market_candles',
        sa.Column('instrument_id', sa.Integer(), nullable=False),
        sa.Column('timeframe', sa.String(length=4), nullable=False),
        sa.Column('open_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('close_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('open', _AMOUNT, nullable=False),
        sa.Column('high', _AMOUNT, nullable=False),
        sa.Column('low', _AMOUNT, nullable=False),
        sa.Column('close', _AMOUNT, nullable=False),
        sa.Column('volume', _AMOUNT, nullable=False),
        sa.Column('turnover', _AMOUNT, nullable=False),
        sa.Column('ingested_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('instrument_id', 'timeframe', 'open_time'),
        sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id']),
        postgresql_partition_by='RANGE (open_time)',
    )
    # No separate lookup index: the primary key (instrument_id, timeframe,
    # open_time) already leads with instrument_id/timeframe, so it serves
    # the "candles for instrument X/timeframe Y in a date range" query
    # pattern directly — a duplicate index would just double storage.

    # A minimal initial set of partitions (last month through next month) so
    # the application can start inserting immediately. Rolling the window
    # forward and dropping expired months is an ongoing operational concern
    # owned by `app.services.partition_manager`, run from the application's
    # background lifecycle — not repeated migration churn.
    for offset in (-1, 0, 1):
        start, end, suffix = _month_bounds(offset)
        op.execute(
            f"CREATE TABLE IF NOT EXISTS market_candles_{suffix} "
            f"PARTITION OF market_candles "
            f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
        )


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS market_candles CASCADE')
    op.drop_index('ix_instruments_symbol', table_name='instruments')
    op.drop_table('instruments')
