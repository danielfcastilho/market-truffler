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


async def test_partition_functions_accept_a_table_override_and_still_noop_on_sqlite(db_session):
    created = await partition_manager.ensure_partitions(
        db_session, months_back=13, table="market_frame_members", partition_column="frame_time"
    )
    dropped = await partition_manager.drop_expired_partitions(
        db_session, retention_days=365, table="market_frame_members"
    )
    assert created == []
    assert dropped == []


def test_month_arithmetic_wraps_year_boundary():
    from datetime import UTC, datetime

    from app.services.partition_manager import _add_months, _partition_name

    start = datetime(2026, 11, 15, tzinfo=UTC)
    assert _add_months(start, 2).month == 1
    assert _add_months(start, 2).year == 2027
    assert _add_months(start, -12).year == 2025

    assert (
        _partition_name("market_candles", datetime(2026, 1, 1, tzinfo=UTC))
        == "market_candles_2026_01"
    )
    assert (
        _partition_name("market_frame_members", datetime(2026, 12, 1, tzinfo=UTC))
        == "market_frame_members_2026_12"
    )


async def test_candle_reference_guard_includes_cross_month_and_stale_context(db_session):
    from datetime import UTC, datetime

    from app.models.frame import MarketFrameMember

    july = datetime(2026, 7, 1, tzinfo=UTC)
    august = datetime(2026, 8, 1, tzinfo=UTC)
    september = datetime(2026, 9, 1, tzinfo=UTC)
    db_session.add(
        MarketFrameMember(
            frame_time=september,
            instrument_id=1,
            open_time_1m=august,
            open_time_5m=august,
            open_time_15m=august,
            open_time_1h=july,
        )
    )
    await db_session.commit()
    assert await partition_manager._has_candle_references(db_session, july, august)
    assert await partition_manager._has_candle_references(db_session, august, september)
    assert not await partition_manager._has_candle_references(
        db_session, datetime(2026, 6, 1, tzinfo=UTC), july
    )


async def test_postgres_retention_preserves_window_and_referenced_month(monkeypatch):
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 19, tzinfo=UTC)

    monkeypatch.setattr(partition_manager, "datetime", Clock)
    session = SimpleNamespace(
        bind=SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
        execute=AsyncMock(
            side_effect=[
                [
                    ("market_candles_2026_06",),
                    ("market_candles_2026_07",),
                    ("market_candles_2026_08",),
                    ("market_candles_2026_09",),
                ],
                None,
                None,
                None,
            ]
        ),
        scalar=AsyncMock(side_effect=[False, True]),
        commit=AsyncMock(),
    )
    dropped = await partition_manager.drop_expired_partitions(session, retention_days=30)
    assert dropped == ["market_candles_2026_06"]
    statements = [str(call.args[0]) for call in session.execute.call_args_list]
    assert [s for s in statements if s.startswith("DROP")] == [
        "DROP TABLE IF EXISTS market_candles_2026_06"
    ]
    assert session.scalar.await_count == 2  # August/September never considered for deletion
    session.commit.assert_awaited_once()
