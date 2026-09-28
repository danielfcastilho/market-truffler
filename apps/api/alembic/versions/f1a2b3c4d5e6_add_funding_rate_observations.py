"""add funding_rate_observations and instruments.funding_synced_from

Revision ID: f1a2b3c4d5e6
Revises: 843a62ca44e6
Create Date: 2026-09-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = '843a62ca44e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Matches app/models/funding_rate.py's `_RATE`.
_RATE = sa.Numeric(30, 12)


def upgrade() -> None:
    op.add_column(
        'instruments',
        sa.Column('funding_synced_from', sa.DateTime(timezone=True), nullable=True),
    )

    # Deliberately NOT partitioned like market_candles/sniffer_results —
    # same reasoning as open_interest_observations: this only needs to
    # cover REQUIRED_WARMUP, not the ~30-day candle retention, so retention
    # is a plain DELETE (see FundingRateRepository.delete_before), not the
    # monthly-partition machinery app.services.partition_manager owns.
    op.create_table(
        'funding_rate_observations',
        sa.Column('instrument_id', sa.Integer(), nullable=False),
        sa.Column('funding_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('funding_rate', _RATE, nullable=False),
        sa.PrimaryKeyConstraint('instrument_id', 'funding_time'),
        sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id']),
    )


def downgrade() -> None:
    op.drop_table('funding_rate_observations')
    op.drop_column('instruments', 'funding_synced_from')
