"""make 5m the canonical base candle timeframe (retire 1m)

Revision ID: a9c4e2f7b3d1
Revises: f1a2b3c4d5e6
Create Date: 2026-10-12 16:00:00.000000

Non-destructive by design — nothing is deleted or dropped here:

- `market_frame_members.open_time_1m` becomes nullable. New members no
  longer reference a 1m candle (the column is no longer mapped by the
  application); rows written before this revision keep their value.
- Every instrument's candle-history watermarks are rewound so the history
  reconciler re-walks the whole retention window at 5m from Bybit: the 5m
  rows already stored were derived locally from 1m candles, and the
  application must not depend on 1m-derived data. `history_synced_through`
  is floored to a 5-minute boundary (catch-up chunks are 5m-aligned) and
  `history_synced_from` is reset to that same instant, so bootstrap fetches
  native 5m candles backward over the whole window again. Re-fetched
  candles are idempotent upserts — the derived 5m rows stay readable until
  each one is overwritten by Bybit's own value, so there is no gap. Coverage
  and readiness on Vitals dip and recover while that runs (minutes).

Existing `timeframe = '1m'` candles are left in place, unread; they age out
with the normal monthly partition retention. Optional manual cleanup (drop
the column, delete 1m rows early) is documented in docs/ARCHITECTURE.md —
deliberately not run automatically.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a9c4e2f7b3d1'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('market_frame_members', 'open_time_1m', nullable=True)
    op.execute(
        "UPDATE instruments SET "
        "history_synced_through = to_timestamp("
        "floor(extract(epoch FROM history_synced_through) / 300) * 300), "
        "history_synced_from = to_timestamp("
        "floor(extract(epoch FROM history_synced_through) / 300) * 300) "
        "WHERE history_synced_through IS NOT NULL"
    )


def downgrade() -> None:
    # Restoring NOT NULL only succeeds if no member was written since the
    # upgrade (those genuinely have no 1m reference, and none is
    # fabricated). The watermark rewind needs no undo: it only causes
    # already-stored history to be re-fetched.
    op.alter_column('market_frame_members', 'open_time_1m', nullable=False)
