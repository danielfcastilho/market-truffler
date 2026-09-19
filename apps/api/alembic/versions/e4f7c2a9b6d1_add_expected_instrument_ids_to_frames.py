"""add expected_instrument_ids snapshot to market_frames

Revision ID: e4f7c2a9b6d1
Revises: d3e8b6a1f5c2
Create Date: 2026-09-19 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e4f7c2a9b6d1'
down_revision: Union[str, None] = 'd3e8b6a1f5c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable first so this works against a table that may already hold
    # rows (matches app/db/types.py's IntArray — native integer[] on
    # PostgreSQL). Existing rows predate this column entirely: they are all
    # already terminal (COMPLETE/PARTIAL — a BUILDING row is only ever
    # in-flight for the few seconds of one grace period), and
    # expected_instrument_ids is only ever read for a frame still in
    # BUILDING status (see FrameSynchronizer._finalize_frame), so backfilling
    # them with an empty array is a safe, inert placeholder — it is never
    # read again and this migration does not alter their expected_instruments
    # count or their COMPLETE/PARTIAL status.
    op.add_column(
        'market_frames',
        sa.Column('expected_instrument_ids', postgresql.ARRAY(sa.Integer()), nullable=True),
    )
    op.execute(
        "UPDATE market_frames SET expected_instrument_ids = ARRAY[]::integer[] "
        "WHERE expected_instrument_ids IS NULL"
    )
    op.alter_column('market_frames', 'expected_instrument_ids', nullable=False)


def downgrade() -> None:
    op.drop_column('market_frames', 'expected_instrument_ids')
