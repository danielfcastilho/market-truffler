from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models.instrument import Instrument
from app.repositories.candle_repository import CandleRepository
from app.services.candle_aggregation import derive_higher_timeframes

FIVE = timedelta(minutes=5)


async def _make_instrument(db_session) -> int:
    now = datetime.now(UTC)
    row = Instrument(
        exchange="bybit",
        symbol="BTCUSDT",
        base_coin="BTC",
        quote_coin="USDT",
        is_active=True,
        first_seen_at=now,
        last_seen_at=now,
        history_target_start=now,
        history_synced_from=now,
        history_synced_through=now,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row.id


def _base_row(
    instrument_id: int,
    open_time: datetime,
    *,
    o="1",
    h="1",
    low="1",
    c="1",
    v="1",
    t="1",
    timeframe="5m",
    duration=FIVE,
) -> dict:
    return {
        "instrument_id": instrument_id,
        "timeframe": timeframe,
        "open_time": open_time,
        "close_time": open_time + duration - timedelta(milliseconds=1),
        "open": Decimal(o),
        "high": Decimal(h),
        "low": Decimal(low),
        "close": Decimal(c),
        "volume": Decimal(v),
        "turnover": Decimal(t),
    }


async def _seed_base(
    repo: CandleRepository, instrument_id: int, start: datetime, count: int
) -> None:
    """`count` consecutive 5m candles from `start`; candle i has open
    100+i, high 101+i, low 99+i, close 100.5+i, volume 10, turnover 1000."""
    rows = [
        _base_row(
            instrument_id,
            start + FIVE * i,
            o=f"{100 + i}.0",
            h=f"{100 + i + 1}.0",
            low=f"{100 + i - 1}.0",
            c=f"{100 + i + 0.5}",
            v="10.0",
            t="1000.0",
        )
        for i in range(count)
    ]
    await repo.upsert_many(rows)


class _Harness:
    """Shrinks the repeated (repo, instrument_id, window) plumbing below."""

    def __init__(self, repo: CandleRepository, instrument_id: int) -> None:
        self.repo = repo
        self.instrument_id = instrument_id

    async def derive(self, start: datetime, end: datetime) -> dict[str, int]:
        return await derive_higher_timeframes(self.repo, self.instrument_id, start, end)

    async def rows(self, timeframe: str, start: datetime, end: datetime) -> list:
        return await self.repo.fetch_range(self.instrument_id, timeframe, start, end)


async def _harness(db_session) -> _Harness:
    instrument_id = await _make_instrument(db_session)
    return _Harness(CandleRepository(db_session), instrument_id)


async def test_15m_aggregation_derives_correct_ohlcv_and_turnover_from_three_5m(db_session):
    h = await _harness(db_session)
    start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    end = start + timedelta(minutes=15)
    await _seed_base(h.repo, h.instrument_id, start, 3)

    derived = await h.derive(start, end)
    assert derived["15m"] == 1

    rows = await h.rows("15m", start, end)
    assert len(rows) == 1
    candle = rows[0]
    assert candle.open_time == start
    assert candle.close_time == end - timedelta(microseconds=1)
    assert candle.open == Decimal("100.0")  # open of first constituent
    assert candle.close == Decimal("102.5")  # close of last constituent
    assert candle.high == Decimal("103.0")  # max high across constituents
    assert candle.low == Decimal("99.0")  # min low across constituents
    assert candle.volume == Decimal("30.0")  # sum of volumes
    assert candle.turnover == Decimal("3000.0")  # sum of turnover


async def test_base_timeframe_is_never_derived(db_session):
    """5m is exchange-observed, never manufactured locally."""
    h = await _harness(db_session)
    start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    await _seed_base(h.repo, h.instrument_id, start, 3)

    derived = await h.derive(start, start + timedelta(minutes=15))

    assert "5m" not in derived
    assert set(derived) == {"15m", "1h", "4h", "24h"}


async def test_leftover_1m_rows_are_never_aggregated(db_session):
    """Pre-migration 1m rows may still sit in market_candles until they
    age out — they must never feed a derived candle."""
    h = await _harness(db_session)
    start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    await h.repo.upsert_many(
        [
            _base_row(
                h.instrument_id,
                start + timedelta(minutes=m),
                timeframe="1m",
                duration=timedelta(minutes=1),
            )
            for m in range(60)
        ]
    )

    derived = await h.derive(start, start + timedelta(hours=1))

    assert all(count == 0 for count in derived.values())
    assert await h.rows("15m", start, start + timedelta(hours=1)) == []


async def test_1h_aggregation_from_twelve_5m(db_session):
    h = await _harness(db_session)
    start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    await _seed_base(h.repo, h.instrument_id, start, 12)

    derived = await h.derive(start, end)
    assert derived["1h"] == 1
    assert derived["15m"] == 4

    rows = await h.rows("1h", start, end)
    assert len(rows) == 1
    assert rows[0].open_time == start
    assert rows[0].close == Decimal("111.5")
    assert rows[0].volume == Decimal("120.0")


async def test_24h_aggregation_from_288_5m(db_session):
    h = await _harness(db_session)
    start = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    end = start + timedelta(hours=24)
    await _seed_base(h.repo, h.instrument_id, start, 288)

    derived = await h.derive(start, end)
    assert derived["24h"] == 1

    rows = await h.rows("24h", start, end)
    assert len(rows) == 1
    assert rows[0].open == Decimal("100.0")
    assert rows[0].close == Decimal("387.5")
    assert rows[0].volume == Decimal("2880.0")


async def test_utc_boundary_alignment_not_relative_to_arbitrary_start(db_session):
    """A 15m window straddling an off-boundary range must still land on
    :00/:15/:30/:45 boundaries, not on whatever candle ingestion started."""
    h = await _harness(db_session)
    # Seed 12:10-12:35 (six 5m candles) — the tail of the 12:00 window
    # (12:10), all of 12:15 (12:15/20/25), and the start of 12:30
    # (12:30/35, incomplete).
    seed_start = datetime(2026, 1, 1, 12, 10, tzinfo=UTC)
    await _seed_base(h.repo, h.instrument_id, seed_start, 6)

    await h.derive(seed_start, datetime(2026, 1, 1, 12, 40, tzinfo=UTC))

    rows = await h.rows(
        "15m", datetime(2026, 1, 1, 11, 0, tzinfo=UTC), datetime(2026, 1, 1, 13, 0, tzinfo=UTC)
    )
    # Only the fully-covered 12:15 window is complete; the partial 12:00
    # and 12:30 windows must not appear.
    assert [r.open_time for r in rows] == [datetime(2026, 1, 1, 12, 15, tzinfo=UTC)]


async def test_incomplete_constituent_set_does_not_produce_aggregate(db_session):
    h = await _harness(db_session)
    start = datetime(2026, 1, 1, 12, 15, tzinfo=UTC)
    end = start + timedelta(minutes=15)
    # Only 2 of 3 constituent 5m candles — 12:20 is missing.
    await h.repo.upsert_many([_base_row(h.instrument_id, start + FIVE * i) for i in (0, 2)])

    derived = await h.derive(start, end)
    assert derived["15m"] == 0
    assert await h.rows("15m", start, end) == []


async def test_recovered_missing_constituent_completes_the_aggregate(db_session):
    """M3 section 14: late recovery of a missing 5m candle must complete the
    15m aggregate that was previously withheld."""
    h = await _harness(db_session)
    start = datetime(2026, 1, 1, 12, 15, tzinfo=UTC)
    end = start + timedelta(minutes=15)

    for i in (0, 2):  # 12:20 missing
        await h.repo.upsert_many([_base_row(h.instrument_id, start + FIVE * i)])
    await h.derive(start, end)
    assert await h.rows("15m", start, end) == []

    # Recovery fills the missing candle...
    await h.repo.upsert_many([_base_row(h.instrument_id, start + FIVE)])
    # ...and re-deriving over the affected range now completes the aggregate.
    derived = await h.derive(start, end)
    assert derived["15m"] == 1
    assert len(await h.rows("15m", start, end)) == 1


async def test_4h_aggregation_derives_correct_ohlcv_and_turnover(db_session):
    h = await _harness(db_session)
    start = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)  # exchange/UTC-aligned 4h boundary
    end = start + timedelta(hours=4)
    await _seed_base(h.repo, h.instrument_id, start, 48)

    derived = await h.derive(start, end)
    assert derived["4h"] == 1

    rows = await h.rows("4h", start, end)
    assert len(rows) == 1
    candle = rows[0]
    assert candle.open_time == start
    assert candle.close_time == end - timedelta(microseconds=1)
    assert candle.open == Decimal("100.0")  # open of first constituent
    assert candle.close == Decimal("147.5")  # close of last (48th) constituent
    assert candle.high == Decimal("148.0")  # max high across constituents
    assert candle.low == Decimal("99.0")  # min low across constituents
    assert candle.volume == Decimal("480.0")  # sum of volumes (48 * 10.0)
    assert candle.turnover == Decimal("48000.0")  # sum of turnover (48 * 1000.0)


async def test_4h_boundaries_align_to_00_04_08_12_16_20_utc(db_session):
    """A 4h window straddling an off-boundary range must still land on the
    fixed exchange/UTC 00:00/04:00/08:00/12:00/16:00/20:00 boundaries, not
    on whatever candle ingestion happened to start at."""
    h = await _harness(db_session)
    # Seed the tail of the 04:00-07:59 bucket (07:50, 07:55) plus all of
    # 08:00-11:59 (48 candles) plus the start of 12:00-15:59 (12:00).
    seed_start = datetime(2026, 1, 1, 7, 50, tzinfo=UTC)
    await _seed_base(h.repo, h.instrument_id, seed_start, 51)

    await h.derive(seed_start, datetime(2026, 1, 1, 12, 5, tzinfo=UTC))

    rows = await h.rows(
        "4h", datetime(2026, 1, 1, 0, 0, tzinfo=UTC), datetime(2026, 1, 2, 0, 0, tzinfo=UTC)
    )
    # Only the fully-covered 08:00-11:59 bucket is complete; the partial
    # 04:00 and 12:00 buckets must not appear.
    assert [r.open_time for r in rows] == [datetime(2026, 1, 1, 8, 0, tzinfo=UTC)]


async def test_incomplete_4h_bucket_is_not_materialized(db_session):
    h = await _harness(db_session)
    start = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    end = start + timedelta(hours=4)
    await _seed_base(h.repo, h.instrument_id, start, 47)  # one candle short of a full bucket

    derived = await h.derive(start, end)
    assert derived["4h"] == 0
    assert await h.rows("4h", start, end) == []


async def test_single_missing_source_5m_candle_prevents_4h_materialization(db_session):
    h = await _harness(db_session)
    start = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    end = start + timedelta(hours=4)
    rows = [
        _base_row(h.instrument_id, start + FIVE * i)
        for i in range(48)
        if i != 29  # a single gap deep inside the bucket
    ]
    await h.repo.upsert_many(rows)

    derived = await h.derive(start, end)
    assert derived["4h"] == 0
    assert await h.rows("4h", start, end) == []

    # Recovery fills the missing candle...
    await h.repo.upsert_many([_base_row(h.instrument_id, start + FIVE * 29)])
    # ...and re-deriving over the affected range now completes the bucket
    # (this is exactly what a bootstrap page or a gap-recovery pass does —
    # both funnel through this same function; see app.services.candle_ingestion).
    derived = await h.derive(start, end)
    assert derived["4h"] == 1
    assert len(await h.rows("4h", start, end)) == 1


async def test_corrected_5m_candle_recomputes_the_affected_4h_aggregate(db_session):
    h = await _harness(db_session)
    start = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    end = start + timedelta(hours=4)
    await _seed_base(h.repo, h.instrument_id, start, 48)
    await h.derive(start, end)

    original = (await h.rows("4h", start, end))[0]
    assert original.close == Decimal("147.5")

    # An exchange-backed correction to the last constituent's close price.
    await h.repo.upsert_many(
        [
            _base_row(
                h.instrument_id,
                start + FIVE * 47,
                o="147.0",
                h="149.0",
                low="99.0",
                c="999.0",
                v="10.0",
                t="1000.0",
            )
        ]
    )
    await h.derive(start, end)

    corrected = (await h.rows("4h", start, end))[0]
    assert corrected.close == Decimal("999.0")


async def test_corrected_5m_candle_recomputes_the_affected_aggregate(db_session):
    h = await _harness(db_session)
    start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    end = start + timedelta(minutes=15)
    await _seed_base(h.repo, h.instrument_id, start, 3)
    await h.derive(start, end)

    original = (await h.rows("15m", start, end))[0]
    assert original.close == Decimal("102.5")

    # An exchange-backed correction to the last constituent's close price.
    await h.repo.upsert_many(
        [
            _base_row(
                h.instrument_id,
                start + FIVE * 2,
                o="102.0",
                h="104.0",
                low="99.0",
                c="999.0",
                v="10.0",
                t="1000.0",
            )
        ]
    )
    await h.derive(start, end)

    corrected = (await h.rows("15m", start, end))[0]
    assert corrected.close == Decimal("999.0")
