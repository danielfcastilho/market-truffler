"""record ingested_at on OI and funding observations; Sniffer analysis provenance

Revision ID: c2d4e6f8a0b1
Revises: a9c4e2f7b3d1
Create Date: 2026-10-24 00:00:00.000000

Nullable and never backfilled: existing rows keep NULL ("availability
unknown"), because the instant they were first stored was not recorded and
must not be invented (see docs/TEMPORAL_CONTRACT.md). Adding a nullable
column without a default is a metadata-only change in Postgres, including
on the partitioned sniffer_results.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c2d4e6f8a0b1'
down_revision: Union[str, None] = 'a9c4e2f7b3d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'open_interest_observations',
        sa.Column('ingested_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'funding_rate_observations',
        sa.Column('ingested_at', sa.DateTime(timezone=True), nullable=True),
    )
    # 'live' or 'recovery'; NULL on results stored before it was recorded.
    op.add_column('sniffer_results', sa.Column('analysis', sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column('sniffer_results', 'analysis')
    op.drop_column('funding_rate_observations', 'ingested_at')
    op.drop_column('open_interest_observations', 'ingested_at')
