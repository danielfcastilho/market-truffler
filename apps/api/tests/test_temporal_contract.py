"""The temporal contract (app.domain.temporal, docs/TEMPORAL_CONTRACT.md):
an OI or funding observation is usable for frame T only if it was observed
by T and stored by T's decision time — identically live and in replay.

These tests reproduce the audit's F-01/F-03 failure: the live analysis ran
while some inputs were not stored yet, a replay later saw them, and the
two disagreed. The parity checks here deliberately change the database
*between* the live run and the replay, which the older same-state parity
test (test_replay.py) structurally could not."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_sniffer_service import _add_instrument, _finalize_complete_frame, _seed_full_hour

from app.domain.temporal import DECISION_DELAY, decision_time
from app.features.engine import FeatureEngine
from app.models.funding_rate import FundingRateObservation
from app.models.open_interest import OpenInterestObservation
from app.repositories.candle_repository import CandleRepository
from app.repositories.frame_repository import FrameRepository
from app.repositories.funding_rate_repository import FundingRateRepository
from app.repositories.open_interest_repository import OpenInterestRepository
from app.repositories.sniffer_repository import SnifferRepository
from app.research.replay import replay_frame
from app.services.boundary_schedule import next_round_at
from app.services.sniffer import Sniffer

T = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)
D = decision_time(T)
ON_TIME = T + timedelta(seconds=25)  # a boundary-aligned round stores bucket T here
LATE = D + timedelta(seconds=1)  # the old unaligned poll could store it minutes later
OI_FEATURES = ("oi_change_5m", "oi_change_15m", "oi_change_1h", "oi_change_4h", "oi_change_24h")
FUNDING_FEATURES = ("funding_rate_current", "funding_rate_24h_avg")


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


async def _frame_with(session_factory, symbol: str) -> list[int]:
    """A finalized one-member frame at T with two hours of candles."""
    iid = await _add_instrument(session_factory, symbol)
    await _seed_full_hour(session_factory, iid, T - timedelta(hours=2))
    await _seed_full_hour(session_factory, iid, T - timedelta(hours=1))
    await _finalize_complete_frame(session_factory, iid, T)
    return [iid]


async def _oi(session_factory, iid: int, observed_at: datetime, value: str, ingested_at) -> None:
    async with session_factory() as session:
        await OpenInterestRepository(session).upsert_many(
            [
                {
                    "instrument_id": iid,
                    "observed_at": observed_at,
                    "open_interest": Decimal(value),
                    "ingested_at": ingested_at,
                }
            ]
        )


async def _funding(session_factory, iid: int, at: datetime, rate: str, ingested_at) -> None:
    async with session_factory() as session:
        await FundingRateRepository(session).upsert_many(
            [
                {
                    "instrument_id": iid,
                    "funding_time": at,
                    "funding_rate": Decimal(rate),
                    "ingested_at": ingested_at,
                }
            ]
        )


async def _live(session_factory) -> dict[str, Decimal | None]:
    """What a live analysis at the decision time measures."""
    async with session_factory() as session:
        frame = await FrameRepository(session).get_frame(T)
        result = await FeatureEngine().run(
            frame,
            CandleRepository(session),
            OpenInterestRepository(session),
            FundingRateRepository(session),
            analyzed_at=D,
        )
    return result.instruments[0].features


async def _replayed(session_factory) -> dict[str, Decimal | None]:
    async with session_factory() as session:
        replayed = await replay_frame(session, T)
    assert replayed is not None
    return replayed.sniffs.instruments[0].features


# -- the contract itself ---------------------------------------------------------------------------


def test_decision_time_is_a_fixed_offset_after_the_boundary():
    assert decision_time(T) == T + DECISION_DELAY
    assert timedelta(seconds=30) < DECISION_DELAY <= timedelta(minutes=2)


def test_reconciler_rounds_align_to_each_boundary_plus_an_offset():
    offset = timedelta(seconds=15)
    assert next_round_at(T, offset) == T + offset
    assert next_round_at(T + timedelta(seconds=14), offset) == T + offset
    assert next_round_at(T + offset, offset) == T + timedelta(minutes=5) + offset
    assert next_round_at(T + timedelta(minutes=4), offset) == T + timedelta(minutes=5) + offset


# -- Open Interest (F-01) -----------------------------------------------------------------------


async def test_a_bucket_stored_after_the_decision_time_is_not_used(session_factory):
    [iid] = await _frame_with(session_factory, "BTCUSDT")
    await _oi(session_factory, iid, T - timedelta(minutes=5), "1100", T - timedelta(minutes=4))
    await _oi(session_factory, iid, T - timedelta(minutes=65), "1000", T - timedelta(minutes=64))
    await _oi(session_factory, iid, T - timedelta(hours=1), "900", T - timedelta(minutes=59))
    await _oi(session_factory, iid, T, "1200", LATE)

    features = await _live(session_factory)

    # Bucket T arrived too late: the anchor is T-5m (and its own T-5m-1h).
    assert features["oi_change_1h"] == Decimal("1100") / Decimal("1000") - 1


async def test_a_bucket_stored_by_the_decision_time_is_used(session_factory):
    [iid] = await _frame_with(session_factory, "BTCUSDT")
    await _oi(session_factory, iid, T - timedelta(minutes=5), "1100", T - timedelta(minutes=4))
    await _oi(session_factory, iid, T - timedelta(hours=1), "900", T - timedelta(minutes=59))
    await _oi(session_factory, iid, T, "1200", ON_TIME)

    assert (await _live(session_factory))["oi_change_1h"] == Decimal("1200") / Decimal("900") - 1


async def test_the_comparison_bucket_obeys_the_same_availability_bound(session_factory):
    [iid] = await _frame_with(session_factory, "BTCUSDT")
    await _oi(session_factory, iid, T, "1200", ON_TIME)
    # Repaired after the fact: T-1h was not available when T was measured.
    await _oi(session_factory, iid, T - timedelta(hours=1), "900", LATE)

    assert (await _live(session_factory))["oi_change_1h"] is None


async def test_legacy_rows_without_ingested_at_replay_exactly_as_before(session_factory):
    """Rows stored before the contract have no recorded availability:
    accepted (unverified), so pre-contract history replays unchanged."""
    [iid] = await _frame_with(session_factory, "BTCUSDT")
    await _oi(session_factory, iid, T, "1200", None)
    await _oi(session_factory, iid, T - timedelta(hours=1), "900", None)

    assert (await _replayed(session_factory))["oi_change_1h"] == Decimal("1200") / Decimal(
        "900"
    ) - 1


async def test_live_and_a_later_replay_agree_after_late_ingestion(session_factory):
    """The F-01 failure: live measured with bucket T missing; the poll
    stored it later; replay used it. Now replay sees what live saw."""
    [iid] = await _frame_with(session_factory, "BTCUSDT")
    for minutes in (5, 10, 15, 20, 60, 65, 240, 245, 1440, 1445):
        observed = T - timedelta(minutes=minutes)
        await _oi(
            session_factory, iid, observed, str(1000 + minutes), observed + timedelta(seconds=25)
        )
    live = await _live(session_factory)
    assert all(live[name] is not None for name in OI_FEATURES)

    # The bucket at T lands after the decision time (late poll, repair).
    await _oi(session_factory, iid, T, "5000", LATE)
    replayed = await _replayed(session_factory)

    assert {name: replayed[name] for name in OI_FEATURES} == {
        name: live[name] for name in OI_FEATURES
    }


# -- Funding (F-03) ------------------------------------------------------------------------------


async def test_a_settlement_at_the_boundary_counts_only_once_stored_by_the_decision_time(
    session_factory,
):
    [iid] = await _frame_with(session_factory, "BTCUSDT")
    await _funding(
        session_factory, iid, T - timedelta(hours=1), "0.0001", T - timedelta(minutes=59)
    )

    await _funding(session_factory, iid, T, "0.0005", LATE)
    late = await _live(session_factory)
    assert late["funding_rate_current"] == Decimal("0.0001")
    assert late["funding_rate_24h_avg"] == Decimal("0.0001")

    async with session_factory() as session:
        await session.execute(
            FundingRateObservation.__table__.update()
            .where(FundingRateObservation.funding_time == T)
            .values(ingested_at=ON_TIME)
        )
        await session.commit()
    on_time = await _live(session_factory)
    assert on_time["funding_rate_current"] == Decimal("0.0005")
    assert on_time["funding_rate_24h_avg"] == Decimal("0.0003")


async def test_funding_live_and_a_later_replay_agree_at_a_settlement_boundary(session_factory):
    [iid] = await _frame_with(session_factory, "BTCUSDT")
    await _funding(session_factory, iid, T - timedelta(hours=8), "0.0002", T - timedelta(hours=7))
    live = await _live(session_factory)
    await _funding(session_factory, iid, T, "-0.003", LATE)  # stored by the next unaligned poll

    replayed = await _replayed(session_factory)

    assert {n: replayed[n] for n in FUNDING_FEATURES} == {n: live[n] for n in FUNDING_FEATURES}
    assert replayed["funding_rate_current"] == Decimal("0.0002")


# -- provenance: ingested_at is first-seen evidence, never rewritten earlier -----------------------


async def test_a_refetch_of_the_same_value_keeps_the_first_ingestion_instant(session_factory):
    iid = await _add_instrument(session_factory, "BTCUSDT")
    await _oi(session_factory, iid, T, "1200", ON_TIME)
    await _oi(session_factory, iid, T, "1200", LATE)
    await _oi(session_factory, iid, T - timedelta(minutes=5), "1100", None)  # legacy row
    await _oi(session_factory, iid, T - timedelta(minutes=5), "1100", LATE)

    async with session_factory() as session:
        rows = {
            r.observed_at: r.ingested_at
            for r in (await session.execute(select(OpenInterestObservation))).scalars()
        }
    assert rows == {T: ON_TIME, T - timedelta(minutes=5): None}


async def test_an_exchange_side_revision_records_when_the_new_value_became_available(
    session_factory,
):
    iid = await _add_instrument(session_factory, "BTCUSDT")
    await _oi(session_factory, iid, T, "1200", ON_TIME)
    await _oi(session_factory, iid, T, "1250", LATE)
    await _funding(session_factory, iid, T, "0.0001", ON_TIME)
    await _funding(session_factory, iid, T, "0.0002", LATE)

    async with session_factory() as session:
        oi = (await session.execute(select(OpenInterestObservation))).scalar_one()
        funding = (await session.execute(select(FundingRateObservation))).scalar_one()
    assert (oi.open_interest, oi.ingested_at) == (Decimal("1250"), LATE)
    assert (funding.funding_rate, funding.ingested_at) == (Decimal("0.0002"), LATE)


# -- Sniffer measures at the decision time, never before -------------------------------------------


async def test_live_analysis_waits_for_the_decision_time(session_factory, monkeypatch):
    [iid] = await _frame_with(session_factory, "BTCUSDT")
    now = T + timedelta(seconds=29)  # the hook fires right after finalization
    waits: list[float] = []

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("app.services.sniffer.datetime", _Clock)
    await Sniffer(session_factory, sleep=fake_sleep).analyze_frame(T)

    assert waits == [(D - now).total_seconds()]
    async with session_factory() as session:
        result = await SnifferRepository(session).get_for_frame(T)
    assert result is not None and result.analysis == "live"


async def test_analysis_after_the_decision_time_does_not_wait(session_factory):
    await _frame_with(session_factory, "BTCUSDT")
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    await Sniffer(session_factory, sleep=fake_sleep).analyze_frame(T)
    assert waits == []


async def test_the_parity_diagnostic_reports_agreement_after_late_ingestion(
    session_factory, monkeypatch
):
    """F-05: the read-only diagnostic compares stored live results with a
    replay done later — here after a late bucket — and finds no mismatch."""
    from app.scripts import parity_diagnostic

    [iid] = await _frame_with(session_factory, "BTCUSDT")
    await _oi(session_factory, iid, T - timedelta(minutes=5), "1100", T - timedelta(minutes=4))
    await _oi(session_factory, iid, T - timedelta(minutes=65), "1000", T - timedelta(minutes=64))
    await Sniffer(session_factory).analyze_frame(T)
    await _oi(session_factory, iid, T, "5000", LATE)
    monkeypatch.setattr(parity_diagnostic, "get_session_factory", lambda: session_factory)

    report = await parity_diagnostic.diagnose(frames=5)

    assert [f["frame_time"] for f in report["frames"]] == [T.isoformat()]
    assert report["frames"][0]["mismatched_values"] == 0
    assert report["features"]["oi_change_1h"] == {"compared": 1, "mismatched": 0, "rate": 0.0}
