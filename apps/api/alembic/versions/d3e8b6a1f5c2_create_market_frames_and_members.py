"""create market_frames and market_frame_members tables

Revision ID: d3e8b6a1f5c2
Revises: c7a1f9e3d2b4
Create Date: 2026-09-19 20:00:00.000000

"""
from datetime import UTC, datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3e8b6a1f5c2'
down_revision: Union[str, None] = 'c7a1f9e3d2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _month_bounds(offset_months: int) -> tuple[datetime, datetime, str]:
    """Same helper as the previous migration — see its docstring."""
    now = datetime.now(UTC)
    year = now.year + (now.month - 1 + offset_months) // 12
    month = (now.month - 1 + offset_months) % 12 + 1
    start = datetime(year, month, 1, tzinfo=UTC)
    end_year = year + (month // 12)
    end_month = month % 12 + 1
    end = datetime(end_year, end_month, 1, tzinfo=UTC)
    return start, end, f"{year:04d}_{month:02d}"


def upgrade() -> None:
    # A small table (~525,600 rows/year) — no partitioning needed at this scale.
    op.create_table(
        'market_frames',
        sa.Column('frame_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expected_instruments', sa.Integer(), nullable=False),
        sa.Column('available_instruments', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finalized_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('frame_time'),
    )

    # The large table (~405M rows/year at today's ~771-instrument universe:
    # one row per instrument per minute). Partitioned by RANGE(frame_time),
    # same monthly scheme and same operational ownership
    # (app.services.partition_manager) as market_candles.
    op.create_table(
        'market_frame_members',
        sa.Column('frame_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('instrument_id', sa.Integer(), nullable=False),
        sa.Column('open_time_1m', sa.DateTime(timezone=True), nullable=False),
        sa.Column('open_time_5m', sa.DateTime(timezone=True), nullable=False),
        sa.Column('open_time_15m', sa.DateTime(timezone=True), nullable=False),
        sa.Column('open_time_1h', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('frame_time', 'instrument_id'),
        sa.ForeignKeyConstraint(['frame_time'], ['market_frames.frame_time']),
        sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id']),
        postgresql_partition_by='RANGE (frame_time)',
    )
    # No separate lookup index: the primary key (frame_time, instrument_id)
    # already leads with frame_time, which is exactly the dominant read
    # ("give me all members of frame T") — a duplicate index would just
    # double storage on the table this milestone is most scale-conscious
    # about.

    for offset in (-1, 0, 1):
        start, end, suffix = _month_bounds(offset)
        op.execute(
            f"CREATE TABLE IF NOT EXISTS market_frame_members_{suffix} "
            f"PARTITION OF market_frame_members "
            f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
        )


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS market_frame_members CASCADE')
    op.drop_table('market_frames')
