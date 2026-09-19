from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.domain.sniffer import SnifferFrameResult, SnifferInstrumentResult
from app.models.frame import MarketFrame as MarketFrameRow
from app.models.instrument import Instrument
from app.models.sniffer import SnifferResult
from app.repositories.sniffer_repository import SnifferRepository

FRAME_TIME = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)
ANALYZED_AT = datetime(2026, 1, 1, 14, 0, 6, tzinfo=UTC)


async def _seed_frame_and_instrument(db_session, symbol: str = "BTCUSDT") -> int:
    now = datetime.now(UTC)
    instrument = Instrument(
        exchange="bybit",
        symbol=symbol,
        base_coin=symbol.removesuffix("USDT"),
        quote_coin="USDT",
        is_active=True,
        first_seen_at=now,
        last_seen_at=now,
    )
    db_session.add(instrument)
    db_session.add(
        MarketFrameRow(
            frame_time=FRAME_TIME,
            expected_instrument_ids=[],
            expected_instruments=0,
            available_instruments=0,
            status="complete",
            created_at=FRAME_TIME,
            finalized_at=FRAME_TIME,
        )
    )
    await db_session.commit()
    await db_session.refresh(instrument)
    return instrument.id


def _result(instrument_id: int, symbol: str, return_5m: Decimal | None) -> SnifferFrameResult:
    return SnifferFrameResult(
        frame_time=FRAME_TIME,
        analyzed_at=ANALYZED_AT,
        instruments=[
            SnifferInstrumentResult(
                instrument_id=instrument_id, symbol=symbol, features={"return_5m": return_5m}
            )
        ],
    )


async def test_save_result_persists_the_value(db_session):
    iid = await _seed_frame_and_instrument(db_session)
    repo = SnifferRepository(db_session)

    await repo.save_result(_result(iid, "BTCUSDT", Decimal("0.01")))

    rows = (await db_session.execute(select(SnifferResult))).scalars().all()
    assert len(rows) == 1
    assert rows[0].metric == "return_5m"
    assert rows[0].value == Decimal("0.01")


async def test_save_result_persists_unavailable_as_a_null_value_row(db_session):
    """Unavailable must remain inspectable: a row exists (Sniffer looked at
    it) with value=NULL, not silently omitted."""
    iid = await _seed_frame_and_instrument(db_session)
    repo = SnifferRepository(db_session)

    await repo.save_result(_result(iid, "BTCUSDT", None))

    rows = (await db_session.execute(select(SnifferResult))).scalars().all()
    assert len(rows) == 1
    assert rows[0].value is None


async def test_save_result_is_idempotent_no_duplicate_rows(db_session):
    iid = await _seed_frame_and_instrument(db_session)
    repo = SnifferRepository(db_session)

    await repo.save_result(_result(iid, "BTCUSDT", Decimal("0.01")))
    await repo.save_result(_result(iid, "BTCUSDT", Decimal("0.01")))  # re-analysis, same frame

    rows = (await db_session.execute(select(SnifferResult))).scalars().all()
    assert len(rows) == 1


async def test_save_result_re_analysis_overwrites_not_duplicates(db_session):
    """Re-analyzing (e.g. a retry) with a *different* value updates the
    existing row rather than creating a second one — deterministic,
    idempotent re-analysis (M5 section 15)."""
    iid = await _seed_frame_and_instrument(db_session)
    repo = SnifferRepository(db_session)

    await repo.save_result(_result(iid, "BTCUSDT", Decimal("0.01")))
    await repo.save_result(_result(iid, "BTCUSDT", Decimal("0.02")))

    rows = (await db_session.execute(select(SnifferResult))).scalars().all()
    assert len(rows) == 1
    assert rows[0].value == Decimal("0.02")


async def test_save_result_empty_instruments_is_a_noop(db_session):
    repo = SnifferRepository(db_session)
    empty = SnifferFrameResult(frame_time=FRAME_TIME, analyzed_at=ANALYZED_AT, instruments=[])

    await repo.save_result(empty)  # must not raise

    rows = (await db_session.execute(select(SnifferResult))).scalars().all()
    assert rows == []


async def test_get_for_frame_returns_none_when_never_analyzed(db_session):
    repo = SnifferRepository(db_session)
    assert await repo.get_for_frame(FRAME_TIME) is None


async def test_get_for_frame_returns_the_saved_result(db_session):
    iid = await _seed_frame_and_instrument(db_session)
    repo = SnifferRepository(db_session)
    await repo.save_result(_result(iid, "BTCUSDT", Decimal("0.015")))

    result = await repo.get_for_frame(FRAME_TIME)

    assert result is not None
    assert result.frame_time == FRAME_TIME
    assert len(result.instruments) == 1
    assert result.instruments[0].symbol == "BTCUSDT"
    assert result.instruments[0].features["return_5m"] == Decimal("0.015")


async def test_get_latest_returns_none_when_nothing_analyzed_yet(db_session):
    repo = SnifferRepository(db_session)
    assert await repo.get_latest() is None


async def test_get_latest_returns_the_most_recent_frame(db_session):
    now = datetime.now(UTC)
    instrument = Instrument(
        exchange="bybit",
        symbol="BTCUSDT",
        base_coin="BTC",
        quote_coin="USDT",
        is_active=True,
        first_seen_at=now,
        last_seen_at=now,
    )
    db_session.add(instrument)
    earlier = FRAME_TIME
    later = datetime(2026, 1, 1, 14, 1, tzinfo=UTC)
    for ft in (earlier, later):
        db_session.add(
            MarketFrameRow(
                frame_time=ft,
                expected_instrument_ids=[],
                expected_instruments=0,
                available_instruments=0,
                status="complete",
                created_at=ft,
                finalized_at=ft,
            )
        )
    await db_session.commit()
    await db_session.refresh(instrument)

    repo = SnifferRepository(db_session)
    await repo.save_result(
        SnifferFrameResult(
            frame_time=earlier,
            analyzed_at=ANALYZED_AT,
            instruments=[
                SnifferInstrumentResult(
                    instrument_id=instrument.id,
                    symbol="BTCUSDT",
                    features={"return_5m": Decimal("0.01")},
                )
            ],
        )
    )
    await repo.save_result(
        SnifferFrameResult(
            frame_time=later,
            analyzed_at=ANALYZED_AT,
            instruments=[
                SnifferInstrumentResult(
                    instrument_id=instrument.id,
                    symbol="BTCUSDT",
                    features={"return_5m": Decimal("0.02")},
                )
            ],
        )
    )

    latest = await repo.get_latest()

    assert latest is not None
    assert latest.frame_time == later
    assert latest.instruments[0].features["return_5m"] == Decimal("0.02")
