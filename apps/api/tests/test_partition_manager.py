"""Partition management is inherently PostgreSQL-only (SQLite has no native
range partitioning). These tests confirm the dialect guard makes both
functions safe, harmless no-ops against the SQLite engine used everywhere
else in this test suite. Real partition creation/drop is verified against a
live PostgreSQL instance in the milestone's manual smoke test — see the
final report.
"""

from app.services import partition_manager


async def test_ensure_partitions_is_a_noop_on_sqlite(db_session):
    created = await partition_manager.ensure_partitions(db_session, months_back=13)
    assert created == []


async def test_drop_expired_partitions_is_a_noop_on_sqlite(db_session):
    dropped = await partition_manager.drop_expired_partitions(db_session, retention_days=365)
    assert dropped == []


def test_month_arithmetic_wraps_year_boundary():
    from datetime import UTC, datetime

    from app.services.partition_manager import _add_months, _partition_name

    start = datetime(2026, 11, 15, tzinfo=UTC)
    assert _add_months(start, 2).month == 1
    assert _add_months(start, 2).year == 2027
    assert _add_months(start, -12).year == 2025

    assert _partition_name(datetime(2026, 1, 1, tzinfo=UTC)) == "market_candles_2026_01"
    assert _partition_name(datetime(2026, 12, 1, tzinfo=UTC)) == "market_candles_2026_12"
