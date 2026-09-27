"""add open_interest_observations and instruments.oi_synced_from

Revision ID: d8626abdac85
Revises: b53245e910f5
Create Date: 2026-09-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8626abdac85'
down_revision: Union[str, None] = 'b53245e910f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Matches app/models/open_interest.py's `_AMOUNT`.
_AMOUNT = sa.Numeric(30, 12)


def upgrade() -> None:
    op.add_column(
        'instruments',
        sa.Column('oi_synced_from', sa.DateTime(timezone=True), nullable=True),
    )

    # Deliberately NOT partitioned like market_candles/sniffer_results — OI
    # only needs to cover REQUIRED_WARMUP (a few days), not the ~30-day
    # candle retention, so the whole table stays small; retention is a
    # plain DELETE (see OpenInterestRepository.delete_before), not the
    # monthly-partition machinery app.services.partition_manager owns.
    op.create_table(
        'open_interest_observations',
        sa.Column('instrument_id', sa.Integer(), nullable=False),
        sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('open_interest', _AMOUNT, nullable=False),
        sa.PrimaryKeyConstraint('instrument_id', 'observed_at'),
        sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id']),
    )


def downgrade() -> None:
    op.drop_table('open_interest_observations')
    op.drop_column('instruments', 'oi_synced_from')
