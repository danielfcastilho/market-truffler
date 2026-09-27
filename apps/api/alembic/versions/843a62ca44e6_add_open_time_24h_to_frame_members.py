"""add open_time_24h to market_frame_members

Revision ID: 843a62ca44e6
Revises: d8626abdac85
Create Date: 2026-09-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '843a62ca44e6'
down_revision: Union[str, None] = 'd8626abdac85'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable, permanently — same convention as open_time_4h (see that
    # migration and app.domain.frame.MarketFrameMember.h24): an
    # already-finalized member row predates 24h tracking entirely, and
    # fabricating what its "latest closed 24h candle" would have been is
    # exactly the kind of invented data this project refuses to produce.
    op.add_column(
        'market_frame_members',
        sa.Column('open_time_24h', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('market_frame_members', 'open_time_24h')
