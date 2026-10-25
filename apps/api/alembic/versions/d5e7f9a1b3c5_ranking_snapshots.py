"""published ranking snapshots

Revision ID: d5e7f9a1b3c5
Revises: c2d4e6f8a0b1
Create Date: 2026-10-25 00:00:00.000000

A new, append-only table; nothing existing is touched. Snapshots exist only
from the first publication after this revision: nothing is backfilled.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd5e7f9a1b3c5'
down_revision: Union[str, None] = 'c2d4e6f8a0b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ranking_snapshots',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('schema_version', sa.Integer(), nullable=False),
        sa.Column('frame_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('analyzed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('analysis', sa.String(16), nullable=True),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('temporal_contract_version', sa.Integer(), nullable=True),
        sa.Column('spec_key', sa.String(64), nullable=False),
        sa.Column('long_candidate', sa.String(80), nullable=True),
        sa.Column('long_candidate_hash', sa.String(64), nullable=True),
        sa.Column('short_candidate', sa.String(80), nullable=True),
        sa.Column('short_candidate_hash', sa.String(64), nullable=True),
        sa.Column('long_qualification', sa.String(80), nullable=True),
        sa.Column('long_qualification_hash', sa.String(64), nullable=True),
        sa.Column('short_qualification', sa.String(80), nullable=True),
        sa.Column('short_qualification_hash', sa.String(64), nullable=True),
        sa.Column('long_qualification_status', sa.String(16), nullable=True),
        sa.Column('short_qualification_status', sa.String(16), nullable=True),
        sa.Column('long_truffles', sa.Integer(), nullable=True),
        sa.Column('short_truffles', sa.Integer(), nullable=True),
        sa.Column('payload_sha256', sa.String(64), nullable=False),
        sa.Column('payload', sa.LargeBinary(), nullable=False),
        sa.UniqueConstraint(
            'frame_time', 'analyzed_at', 'spec_key', name='uq_ranking_snapshot_publication'
        ),
    )
    op.create_index('ix_ranking_snapshots_frame_time', 'ranking_snapshots', ['frame_time'])
    op.create_index('ix_ranking_snapshots_published', 'ranking_snapshots', ['published_at', 'id'])


def downgrade() -> None:
    op.drop_table('ranking_snapshots')
