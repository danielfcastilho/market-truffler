"""add open_time_4h to market_frame_members

Revision ID: b53245e910f5
Revises: f8a3d5e1c9b7
Create Date: 2026-09-19 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b53245e910f5'
down_revision: Union[str, None] = 'f8a3d5e1c9b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable, permanently — unlike expected_instrument_ids (see the prior
    # migration), there is no honest backfill value here: an already-
    # finalized member row predates 4h tracking entirely, and fabricating
    # what its "latest closed 4h candle" would have been is exactly the
    # kind of invented data this project refuses to produce. See
    # app.domain.frame.MarketFrameMember.h4 for why 4h is deliberately not
    # part of the COMPLETE/PARTIAL membership gate either: NULL here simply
    # means "this member predates 4h, or its first 4h bucket hadn't closed
    # yet" — never an error.
    op.add_column(
        'market_frame_members',
        sa.Column('open_time_4h', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('market_frame_members', 'open_time_4h')
