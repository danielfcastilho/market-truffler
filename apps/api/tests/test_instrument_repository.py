from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domain.market import Instrument as DomainInstrument
from app.models.instrument import Instrument
from app.repositories.instrument_repository import InstrumentRepository

RETENTION_DAYS = 365


async def test_new_symbol_is_inserted_with_watermarks_anchored_to_now(db_session):
    repo = InstrumentRepository(db_session)
    now = datetime.now(UTC)

    active, newly_added = await repo.reconcile_universe(
        [DomainInstrument(symbol="BTCUSDT", base_coin="BTC", quote_coin="USDT")],
        now=now,
        retention_days=RETENTION_DAYS,
    )

    assert newly_added == ["BTCUSDT"]
    assert len(active) == 1
    row = active[0]
    assert row.symbol == "BTCUSDT"
    assert row.is_active is True
    assert row.first_seen_at == now
    assert row.history_synced_from == now
    assert row.history_synced_through == now
    assert row.history_target_start == now - timedelta(days=RETENTION_DAYS)


async def test_previously_known_symbol_keeps_its_watermark_progress(db_session):
    repo = InstrumentRepository(db_session)
    first_seen = datetime.now(UTC) - timedelta(days=5)
    await repo.reconcile_universe(
        [DomainInstrument(symbol="BTCUSDT", base_coin="BTC", quote_coin="USDT")],
        now=first_seen,
        retention_days=RETENTION_DAYS,
    )

    # Simulate bootstrap progress having been made since first discovery.
    row = (await db_session.execute(select(Instrument))).scalars().one()
    row.history_synced_from = first_seen - timedelta(days=100)
    await db_session.commit()

    later = first_seen + timedelta(hours=1)
    active, newly_added = await repo.reconcile_universe(
        [DomainInstrument(symbol="BTCUSDT", base_coin="BTC", quote_coin="USDT")],
        now=later,
        retention_days=RETENTION_DAYS,
    )

    assert newly_added == []
    assert len(active) == 1
    assert active[0].last_seen_at == later
    # Progress already made must not be reset by a later reconciliation pass.
    assert active[0].history_synced_from == first_seen - timedelta(days=100)


async def test_symbol_missing_from_a_later_pass_is_marked_inactive_not_deleted(db_session):
    repo = InstrumentRepository(db_session)
    now = datetime.now(UTC)
    await repo.reconcile_universe(
        [
            DomainInstrument(symbol="BTCUSDT", base_coin="BTC", quote_coin="USDT"),
            DomainInstrument(symbol="DELISTEDUSDT", base_coin="DELISTED", quote_coin="USDT"),
        ],
        now=now,
        retention_days=RETENTION_DAYS,
    )

    active, _ = await repo.reconcile_universe(
        [DomainInstrument(symbol="BTCUSDT", base_coin="BTC", quote_coin="USDT")],
        now=now + timedelta(minutes=15),
        retention_days=RETENTION_DAYS,
    )

    assert [i.symbol for i in active] == ["BTCUSDT"]

    all_rows = (await db_session.execute(select(Instrument))).scalars().all()
    assert {r.symbol for r in all_rows} == {"BTCUSDT", "DELISTEDUSDT"}  # never deleted
    delisted = next(r for r in all_rows if r.symbol == "DELISTEDUSDT")
    assert delisted.is_active is False


async def test_instrument_reactivated_after_returning_to_the_universe(db_session):
    repo = InstrumentRepository(db_session)
    now = datetime.now(UTC)
    await repo.reconcile_universe(
        [DomainInstrument(symbol="BTCUSDT", base_coin="BTC", quote_coin="USDT")],
        now=now,
        retention_days=RETENTION_DAYS,
    )
    await repo.reconcile_universe([], now=now + timedelta(minutes=1), retention_days=RETENTION_DAYS)

    row = (await db_session.execute(select(Instrument))).scalars().one()
    assert row.is_active is False

    active, newly_added = await repo.reconcile_universe(
        [DomainInstrument(symbol="BTCUSDT", base_coin="BTC", quote_coin="USDT")],
        now=now + timedelta(minutes=2),
        retention_days=RETENTION_DAYS,
    )

    assert newly_added == []  # already known, just reactivated
    assert [i.symbol for i in active] == ["BTCUSDT"]
