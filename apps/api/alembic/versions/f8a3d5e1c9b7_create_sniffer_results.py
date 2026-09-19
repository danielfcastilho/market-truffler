"""create sniffer_results table

Revision ID: f8a3d5e1c9b7
Revises: e4f7c2a9b6d1
Create Date: 2026-09-19 22:00:00.000000

"""
from datetime import UTC, datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8a3d5e1c9b7'
down_revision: Union[str, None] = 'e4f7c2a9b6d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Matches app/models/sniffer.py's `_VALUE`.
_VALUE = sa.Numeric(30, 12)


def _month_bounds(offset_months: int) -> tuple[datetime, datetime, str]:
    """Same helper as the market_candles/market_frame_members migrations."""
    now = datetime.now(UTC)
    year = now.year + (now.month - 1 + offset_months) // 12
    month = (now.month - 1 + offset_months) % 12 + 1
    start = datetime(year, month, 1, tzinfo=UTC)
    end_year = year + (month // 12)
    end_month = month % 12 + 1
    end = datetime(end_year, end_month, 1, tzinfo=UTC)
    return start, end, f"{year:04d}_{month:02d}"


def upgrade() -> None:
    # Partitioned by RANGE(frame_time), monthly, same as market_candles and
    # market_frame_members: one row per instrument per metric per minute is
    # real per-minute scale, not a small administrative table.
    op.create_table(
        'sniffer_results',
        sa.Column('frame_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('instrument_id', sa.Integer(), nullable=False),
        sa.Column('metric', sa.String(length=32), nullable=False),
        sa.Column('value', _VALUE, nullable=True),
        sa.Column('calculated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('frame_time', 'instrument_id', 'metric'),
        sa.ForeignKeyConstraint(['frame_time'], ['market_frames.frame_time']),
        sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id']),
        postgresql_partition_by='RANGE (frame_time)',
    )
    # No separate lookup index: the primary key (frame_time, instrument_id,
    # metric) already leads with frame_time, which is exactly the dominant
    # read ("give me Sniffer's results for frame T").

    for offset in (-1, 0, 1):
        start, end, suffix = _month_bounds(offset)
        op.execute(
            f"CREATE TABLE IF NOT EXISTS sniffer_results_{suffix} "
            f"PARTITION OF sniffer_results "
            f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
        )


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS sniffer_results CASCADE')
