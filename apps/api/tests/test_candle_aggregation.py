from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models.instrument import Instrument
from app.repositories.candle_repository import CandleRepository
from app.services.candle_aggregation import derive_higher_timeframes


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


def _minute_row(
    instrument_id: int, open_time: datetime, *, o="1", h="1", low="1", c="1", v="1", t="1"
) -> dict:
    return {
        "instrument_id": instrument_id,
        "timeframe": "1m",
        "open_time": open_time,
        "close_time": open_time + timedelta(minutes=1) - timedelta(microseconds=1),
        "open": Decimal(o),
        "high": Decimal(h),
        "low": Decimal(low),
        "close": Decimal(c),
        "volume": Decimal(v),
        "turnover": Decimal(t),
    }


async def _seed_minutes(
    repo: CandleRepository, instrument_id: int, start: datetime, count: int
) -> None:
    rows = [
        _minute_row(
            instrument_id,
            start + timedelta(minutes=i),
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


async def test_5m_aggregation_derives_correct_ohlcv_and_turnover(db_session):
    h = await _harness(db_session)
    start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    end = start + timedelta(minutes=5)
    await _seed_minutes(h.repo, h.instrument_id, start, 5)

    derived = await h.derive(start, end)
    assert derived["5m"] == 1

    rows = await h.rows("5m", start, end)
    assert len(rows) == 1
    candle = rows[0]
    assert candle.open_time == start
    assert candle.open == Decimal("100.0")  # open of first constituent
    assert candle.close == Decimal("104.5")  # close of last constituent
    assert candle.high == Decimal("105.0")  # max high across constituents
    assert candle.low == Decimal("99.0")  # min low across constituents
    assert candle.volume == Decimal("50.0")  # sum of volumes
    assert candle.turnover == Decimal("5000.0")  # sum of turnover


async def test_15m_aggregation(db_session):
    h = await _harness(db_session)
    start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    end = start + timedelta(minutes=15)
    await _seed_minutes(h.repo, h.instrument_id, start, 15)

    derived = await h.derive(start, end)
    assert derived["15m"] == 1

    rows = await h.rows("15m", start, end)
    assert len(rows) == 1
    assert rows[0].open_time == start
    assert rows[0].close == Decimal("114.5")


async def test_1h_aggregation(db_session):
    h = await _harness(db_session)
    start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    await _seed_minutes(h.repo, h.instrument_id, start, 60)

    derived = await h.derive(start, end)
    assert derived["1h"] == 1

    rows = await h.rows("1h", start, end)
    assert len(rows) == 1
    assert rows[0].open_time == start
    assert rows[0].close == Decimal("159.5")


async def test_utc_boundary_alignment_not_relative_to_arbitrary_start(db_session):
    """A 5m window straddling an off-boundary range must still land on
    :00/:05/:10... boundaries, not on whatever minute ingestion started."""
    h = await _harness(db_session)
    # Seed minutes 12:03-12:12 (10 minutes) — covers the tail of one 5m
    # window (12:00-12:04) and all of the next (12:05-12:09), plus the start
    # of a third (12:10-12:14, incomplete).
    seed_start = datetime(2026, 1, 1, 12, 3, tzinfo=UTC)
    await _seed_minutes(h.repo, h.instrument_id, seed_start, 10)

    await h.derive(seed_start, datetime(2026, 1, 1, 12, 13, tzinfo=UTC))

    rows = await h.rows(
        "5m", datetime(2026, 1, 1, 11, 0, tzinfo=UTC), datetime(2026, 1, 1, 13, 0, tzinfo=UTC)
    )
    # Only the fully-covered 12:05-12:09 window is complete; the partial
    # 12:00 and 12:10 windows must not appear.
    assert [r.open_time for r in rows] == [datetime(2026, 1, 1, 12, 5, tzinfo=UTC)]


async def test_incomplete_constituent_set_does_not_produce_aggregate(db_session):
    h = await _harness(db_session)
    start = datetime(2026, 1, 1, 12, 5, tzinfo=UTC)
    end = start + timedelta(minutes=5)
    # Only 4 of 5 constituent minutes — offset 2 (12:07) is missing.
    rows = [_minute_row(h.instrument_id, start + timedelta(minutes=m)) for m in (0, 1, 3, 4)]
    await h.repo.upsert_many(rows)

    derived = await h.derive(start, end)
    assert derived["5m"] == 0
    assert await h.rows("5m", start, end) == []


async def test_recovered_missing_constituent_completes_the_aggregate(db_session):
    """M3 section 14: late recovery of a missing minute must complete the
    5m aggregate that was previously withheld."""
    h = await _harness(db_session)
    start = datetime(2026, 1, 1, 12, 5, tzinfo=UTC)
    end = start + timedelta(minutes=5)

    for m in (0, 1, 3, 4):  # minute 2 (12:07) missing
        await h.repo.upsert_many([_minute_row(h.instrument_id, start + timedelta(minutes=m))])
    await h.derive(start, end)
    assert await h.rows("5m", start, end) == []

    # Recovery fills the missing minute...
    await h.repo.upsert_many([_minute_row(h.instrument_id, start + timedelta(minutes=2))])
    # ...and re-deriving over the affected range now completes the aggregate.
    derived = await h.derive(start, end)
    assert derived["5m"] == 1
    assert len(await h.rows("5m", start, end)) == 1


async def test_4h_aggregation_derives_correct_ohlcv_and_turnover(db_session):
    h = await _harness(db_session)
    start = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)  # exchange/UTC-aligned 4h boundary
    end = start + timedelta(hours=4)
    await _seed_minutes(h.repo, h.instrument_id, start, 240)

    derived = await h.derive(start, end)
    assert derived["4h"] == 1

    rows = await h.rows("4h", start, end)
    assert len(rows) == 1
    candle = rows[0]
    assert candle.open_time == start
    assert candle.close_time == end - timedelta(microseconds=1)
    assert candle.open == Decimal("100.0")  # open of first constituent
    assert candle.close == Decimal("339.5")  # close of last (240th) constituent
    assert candle.high == Decimal("340.0")  # max high across constituents
    assert candle.low == Decimal("99.0")  # min low across constituents
    assert candle.volume == Decimal("2400.0")  # sum of volumes (240 * 10.0)
    assert candle.turnover == Decimal("240000.0")  # sum of turnover (240 * 1000.0)


async def test_4h_boundaries_align_to_00_04_08_12_16_20_utc(db_session):
    """A 4h window straddling an off-boundary range must still land on the
    fixed exchange/UTC 00:00/04:00/08:00/12:00/16:00/20:00 boundaries, not
    on whatever minute ingestion happened to start at."""
    h = await _harness(db_session)
    # Seed the tail of the 04:00-07:59 bucket plus all of 08:00-11:59 plus
    # the start of the still-forming 12:00-15:59 bucket.
    seed_start = datetime(2026, 1, 1, 7, 50, tzinfo=UTC)
    await _seed_minutes(h.repo, h.instrument_id, seed_start, 250)  # through 12:00

    await h.derive(seed_start, datetime(2026, 1, 1, 12, 0, tzinfo=UTC))

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
    await _seed_minutes(h.repo, h.instrument_id, start, 239)  # one minute short of a full bucket

    derived = await h.derive(start, end)
    assert derived["4h"] == 0
    assert await h.rows("4h", start, end) == []


async def test_single_missing_source_1m_candle_prevents_4h_materialization(db_session):
    h = await _harness(db_session)
    start = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    end = start + timedelta(hours=4)
    rows = [
        _minute_row(h.instrument_id, start + timedelta(minutes=m))
        for m in range(240)
        if m != 137  # a single gap deep inside the bucket
    ]
    await h.repo.upsert_many(rows)

    derived = await h.derive(start, end)
    assert derived["4h"] == 0
    assert await h.rows("4h", start, end) == []

    # Recovery fills the missing minute...
    await h.repo.upsert_many([_minute_row(h.instrument_id, start + timedelta(minutes=137))])
    # ...and re-deriving over the affected range now completes the bucket
    # (this is exactly what a bootstrap page or a gap-recovery pass does —
    # both funnel through this same function; see app.services.candle_ingestion).
    derived = await h.derive(start, end)
    assert derived["4h"] == 1
    assert len(await h.rows("4h", start, end)) == 1


async def test_corrected_1m_candle_recomputes_the_affected_4h_aggregate(db_session):
    h = await _harness(db_session)
    start = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    end = start + timedelta(hours=4)
    await _seed_minutes(h.repo, h.instrument_id, start, 240)
    await h.derive(start, end)

    original = (await h.rows("4h", start, end))[0]
    assert original.close == Decimal("339.5")

    # An exchange-backed correction to the last constituent's close price.
    await h.repo.upsert_many(
        [
            _minute_row(
                h.instrument_id,
                start + timedelta(minutes=239),
                o="339.0",
                h="341.0",
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


async def test_corrected_1m_candle_recomputes_the_affected_aggregate(db_session):
    h = await _harness(db_session)
    start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    end = start + timedelta(minutes=5)
    await _seed_minutes(h.repo, h.instrument_id, start, 5)
    await h.derive(start, end)

    original = (await h.rows("5m", start, end))[0]
    assert original.close == Decimal("104.5")

    # An exchange-backed correction to the last constituent's close price.
    await h.repo.upsert_many(
        [
            _minute_row(
                h.instrument_id,
                start + timedelta(minutes=4),
                o="104.0",
                h="106.0",
                low="99.0",
                c="999.0",
                v="10.0",
                t="1000.0",
            )
        ]
    )
    await h.derive(start, end)

    corrected = (await h.rows("5m", start, end))[0]
    assert corrected.close == Decimal("999.0")
