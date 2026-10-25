"""Partition management is inherently PostgreSQL-only (SQLite has no native
range partitioning). These tests confirm the dialect guard makes both
functions safe, harmless no-ops against the SQLite engine used everywhere
else in this test suite. Real partition creation/drop is verified against a
live PostgreSQL instance in tests/test_partition_locks_postgres.py
(opt-in via TEST_POSTGRES_URL).
"""

from datetime import UTC, datetime

from app.services import partition_manager


async def test_ensure_partitions_is_a_noop_on_sqlite(db_session):
    created = await partition_manager.ensure_partitions(
        db_session, months=[datetime(2026, 10, 1, tzinfo=UTC)]
    )
    assert created == []


async def test_drop_expired_partitions_is_a_noop_on_sqlite(db_session):
    dropped = await partition_manager.drop_expired_partitions(db_session, retention_days=365)
    assert dropped == []


async def test_partition_functions_accept_a_table_override_and_still_noop_on_sqlite(db_session):
    created = await partition_manager.ensure_partitions(
        db_session, months=[datetime(2026, 10, 1, tzinfo=UTC)], table="market_frame_members"
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


async def test_candle_reference_guard_includes_4h_references(db_session):
    """open_time_4h participates in the retention guard exactly like the
    four gating timeframe columns — retention handles 4h consistently even
    though it's nullable (see MarketFrameMember.h4)."""
    from datetime import UTC, datetime

    from app.models.frame import MarketFrameMember

    march = datetime(2026, 3, 1, tzinfo=UTC)
    april = datetime(2026, 4, 1, tzinfo=UTC)
    september = datetime(2026, 9, 1, tzinfo=UTC)
    db_session.add(
        MarketFrameMember(
            frame_time=september,
            instrument_id=1,
            open_time_5m=september,
            open_time_15m=september,
            open_time_1h=september,
            open_time_4h=march,  # far older than the other three — only 4h references this month
        )
    )
    await db_session.commit()
    assert await partition_manager._has_candle_references(db_session, march, april)
    assert not await partition_manager._has_candle_references(
        db_session, datetime(2026, 2, 1, tzinfo=UTC), march
    )


async def test_candle_reference_guard_tolerates_null_open_time_4h(db_session):
    """A member with no 4h reference at all (open_time_4h IS NULL) must
    never itself satisfy the guard — a NULL comparison is simply never
    true, exactly as intended."""
    from datetime import UTC, datetime

    from app.models.frame import MarketFrameMember

    june = datetime(2026, 6, 1, tzinfo=UTC)
    july = datetime(2026, 7, 1, tzinfo=UTC)
    db_session.add(
        MarketFrameMember(
            frame_time=june,
            instrument_id=1,
            open_time_5m=datetime(2020, 1, 1, tzinfo=UTC),
            open_time_15m=datetime(2020, 1, 1, tzinfo=UTC),
            open_time_1h=datetime(2020, 1, 1, tzinfo=UTC),
            open_time_4h=None,
        )
    )
    await db_session.commit()
    assert not await partition_manager._has_candle_references(db_session, june, july)


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
        # June: unreferenced before and under the locks; July: referenced at
        # the unlocked pre-check, so it is kept without being re-checked.
        scalar=AsyncMock(side_effect=[False, False, True]),
        commit=AsyncMock(),
    )
    dropped = await partition_manager.drop_expired_partitions(session, retention_days=30)
    assert dropped == ["market_candles_2026_06"]
    statements = [str(call.args[0]) for call in session.execute.call_args_list]
    assert [s for s in statements if s.startswith("DROP")] == [
        "DROP TABLE IF EXISTS market_candles_2026_06"
    ]
    assert session.scalar.await_count == 3  # August/September never considered for deletion
    assert sum(s.startswith("LOCK TABLE") for s in statements) == 2
    session.commit.assert_awaited_once()


def test_retained_months_match_what_retention_keeps():
    """Startup on 2026-10-25 used to create July and August (months_back=3),
    which retention dropped two seconds later — each creation needing an
    ACCESS EXCLUSIVE lock. Only retained months are ever created now."""
    from app.services.partition_manager import retained_months

    now = datetime(2026, 10, 25, 14, 28, tzinfo=UTC)
    assert retained_months(now, 30) == [
        datetime(2026, 9, 1, tzinfo=UTC),
        datetime(2026, 10, 1, tzinfo=UTC),
        datetime(2026, 11, 1, tzinfo=UTC),
    ]
    assert retained_months(datetime(2026, 12, 31, tzinfo=UTC), 1)[-1] == datetime(
        2027, 1, 1, tzinfo=UTC
    )


def test_error_categories_separate_contention_from_faults():
    from sqlalchemy.exc import OperationalError, ProgrammingError

    from app.services.partition_manager import RETRYABLE_CATEGORIES, error_category

    class _Orig(Exception):
        def __init__(self, sqlstate):
            self.sqlstate = sqlstate

    def err(cls, sqlstate):
        return cls("stmt", {}, _Orig(sqlstate))

    assert error_category(err(OperationalError, "55P03")) == "lock_timeout"
    assert error_category(err(OperationalError, "40P01")) == "deadlock"
    assert error_category(err(ProgrammingError, "42P01")) == "missing_schema"
    assert error_category(err(ProgrammingError, "42501")) == "permission"
    assert error_category(err(OperationalError, "08006")) == "unexpected"
    assert error_category(err(OperationalError, None)) == "unexpected"
    assert error_category(RuntimeError("bug")) == "unexpected"
    assert RETRYABLE_CATEGORIES == {"lock_timeout", "deadlock"}
