import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pyarrow.parquet as pq
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.features.engine import FEATURES
from app.models.instrument import Instrument
from app.repositories.candle_repository import CandleRepository
from app.repositories.frame_repository import FrameRepository
from app.research import dataset as dataset_module
from app.research.dataset import (
    DATASET_SCHEMA_VERSION,
    DatasetIntegrityError,
    IncompatibleDatasetError,
    aligned_candidate_times,
    code_fingerprint,
    file_sha256,
    generate_dataset,
    read_dataset,
)
from app.services.candle_aggregation import derive_higher_timeframes

FEATURE_NAMES = {f.name for f in FEATURES}


# -- aligned_candidate_times: deterministic, start-independent grid --------------


def test_grid_is_anchored_to_absolute_time_not_to_start():
    base = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    wide = aligned_candidate_times(base, base + timedelta(hours=1), 15)
    narrower = aligned_candidate_times(base + timedelta(minutes=5), base + timedelta(hours=1), 15)

    assert wide == [
        base,
        base + timedelta(minutes=15),
        base + timedelta(minutes=30),
        base + timedelta(minutes=45),
    ]
    # Same absolute grid points, just clipped to the narrower start — 00:00
    # is dropped because it's < the new start, never shifted to 00:05/00:20.
    assert narrower == wide[1:]


def test_end_is_exclusive():
    base = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    result = aligned_candidate_times(base, base + timedelta(minutes=30), 15)
    assert result == [base, base + timedelta(minutes=15)]


def test_a_start_that_is_not_on_the_grid_advances_to_the_next_aligned_minute():
    base = datetime(2026, 1, 1, 0, 7, tzinfo=UTC)  # 00:07, interval 15
    result = aligned_candidate_times(base, base + timedelta(minutes=30), 15)
    assert result == [
        datetime(2026, 1, 1, 0, 15, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 30, tzinfo=UTC),
    ]


def test_nonpositive_interval_raises():
    base = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        aligned_candidate_times(base, base + timedelta(hours=1), 0)


@pytest.mark.parametrize("interval", [1, 3, 7, 14])
def test_interval_not_a_multiple_of_5_minutes_raises(interval):
    """Every sampled frame_time must be a 5m boundary — an interval like
    7 would land on 00:07, 00:14, ... which are never decision points."""
    base = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="multiple of 5"):
        aligned_candidate_times(base, base + timedelta(hours=1), interval)


@pytest.mark.parametrize("interval", [5, 10, 15, 60])
def test_every_grid_point_is_a_5m_boundary(interval):
    base = datetime(2026, 1, 1, 0, 3, tzinfo=UTC)  # deliberately off-boundary start
    result = aligned_candidate_times(base, base + timedelta(hours=3), interval)
    assert result
    assert all(t.minute % 5 == 0 and t.second == 0 and t.microsecond == 0 for t in result)


# -- generate_dataset: end-to-end -------------------------------------------------

FIRST_FRAME = datetime(2026, 1, 1, 15, 0, tzinfo=UTC)
SECOND_FRAME = FIRST_FRAME + timedelta(minutes=15)
HORIZONS = {"15m": timedelta(minutes=15)}


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


async def _add_instrument(session_factory, symbol: str) -> int:
    async with session_factory() as session:
        now = datetime.now(UTC)
        row = Instrument(
            exchange="bybit",
            symbol=symbol,
            base_coin=symbol.removesuffix("USDT"),
            quote_coin="USDT",
            is_active=True,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id


async def _seed_5m_range(
    session_factory, instrument_id: int, start: datetime, minutes: int, base_price: int
) -> None:
    """`minutes // 5` contiguous 5m candles from `start`; candle i's
    OHLC is `base_price + i`."""
    async with session_factory() as session:
        candle_repo = CandleRepository(session)
        rows = [
            {
                "instrument_id": instrument_id,
                "timeframe": "5m",
                "open_time": start + timedelta(minutes=5 * i),
                "close_time": start + timedelta(minutes=5 * i + 5) - timedelta(milliseconds=1),
                "open": Decimal(str(base_price + i)),
                "high": Decimal(str(base_price + i)),
                "low": Decimal(str(base_price + i)),
                "close": Decimal(str(base_price + i)),
                "volume": Decimal("1"),
                "turnover": Decimal("1"),
            }
            for i in range(minutes // 5)
        ]
        await candle_repo.upsert_many(rows)
        await derive_higher_timeframes(
            candle_repo, instrument_id, start, start + timedelta(minutes=minutes)
        )


async def _finalize_frame(session_factory, instrument_id: int, frame_time: datetime) -> None:
    async with session_factory() as session:
        candle_repo = CandleRepository(session)
        frame_repo = FrameRepository(session)
        await frame_repo.create_building(
            frame_time, expected_instrument_ids=[instrument_id], now=datetime.now(UTC)
        )
        m5 = await candle_repo.fetch_latest_closed_per_instrument(
            "5m", [instrument_id], frame_time - timedelta(minutes=5)
        )
        m15 = await candle_repo.fetch_latest_closed_per_instrument(
            "15m", [instrument_id], frame_time - timedelta(minutes=15)
        )
        h1 = await candle_repo.fetch_latest_closed_per_instrument(
            "1h", [instrument_id], frame_time - timedelta(minutes=60)
        )
        if instrument_id not in m5 or instrument_id not in m15 or instrument_id not in h1:
            await frame_repo.finalize(frame_time, [], now=datetime.now(UTC))
            return
        member = {
            "frame_time": frame_time,
            "instrument_id": instrument_id,
            "open_time_5m": m5[instrument_id].open_time,
            "open_time_15m": m15[instrument_id].open_time,
            "open_time_1h": h1[instrument_id].open_time,
        }
        await frame_repo.finalize(frame_time, [member], now=datetime.now(UTC))


async def test_generate_dataset_end_to_end(session_factory, tmp_path):
    btc = await _add_instrument(session_factory, "BTCUSDT")
    # 90 minutes of contiguous 5m history (13:45-15:15) covers both frames'
    # m5..h1 anchors and the first frame's forward_return_15m target.
    await _seed_5m_range(session_factory, btc, FIRST_FRAME - timedelta(minutes=75), 90, 100)
    await _finalize_frame(session_factory, btc, FIRST_FRAME)
    await _finalize_frame(session_factory, btc, SECOND_FRAME)

    output_dir = tmp_path / "research_v1"
    manifest = await generate_dataset(
        session_factory,
        start=FIRST_FRAME,
        end=SECOND_FRAME + timedelta(minutes=15),
        interval_minutes=15,
        horizons=HORIZONS,
        output_dir=output_dir,
    )

    assert manifest["frame_times_candidate"] == 2  # FIRST_FRAME, SECOND_FRAME
    assert manifest["frame_times_processed"] == 2
    assert manifest["frame_times_missing"] == 0
    assert manifest["row_count"] == 2  # 1 instrument x 2 frames
    assert manifest["instrument_count"] == 1
    assert set(manifest["feature_names"]) == FEATURE_NAMES
    assert manifest["sample_interval_minutes"] == 15
    assert manifest["horizons_minutes"] == {"15m": 15}
    assert manifest["dataset_schema_version"] == DATASET_SCHEMA_VERSION
    assert manifest["base_timeframe"] == "5m"
    assert "5m candle" in manifest["label_definition"]

    assert output_dir.exists()
    table = pq.read_table(output_dir / "data.parquet")
    columns = set(table.column_names)
    assert {"frame_time", "instrument_id", "symbol"} <= columns
    assert FEATURE_NAMES <= columns
    assert "forward_return_15m" in columns
    assert "forward_relative_return_15m" in columns
    assert table.num_rows == 2

    # FIRST_FRAME 15:00: P(T) = 14:55 candle (i=14 -> 114); P(T+15m) = the
    # 15:10 candle, closing 15:20 (i=17 -> 117). SECOND_FRAME 15:15 has no
    # candle closing at 15:30 seeded -> null, never approximated.
    rows = sorted(table.to_pylist(), key=lambda r: r["frame_time"])
    assert rows[0]["forward_return_15m"] == pytest.approx(117 / 114 - 1)
    assert rows[1]["forward_return_15m"] is None

    on_disk_manifest = json.loads((output_dir / "manifest.json").read_text())
    assert on_disk_manifest == manifest


async def test_a_missing_aligned_frame_is_skipped_and_counted_not_substituted(
    session_factory, tmp_path
):
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_5m_range(session_factory, btc, FIRST_FRAME - timedelta(minutes=75), 90, 100)
    await _finalize_frame(session_factory, btc, FIRST_FRAME)
    # SECOND_FRAME is deliberately never finalized -> no market_frames row.

    output_dir = tmp_path / "research_v1"
    manifest = await generate_dataset(
        session_factory,
        start=FIRST_FRAME,
        end=SECOND_FRAME + timedelta(minutes=15),
        interval_minutes=15,
        horizons=HORIZONS,
        output_dir=output_dir,
    )

    assert manifest["frame_times_candidate"] == 2
    assert manifest["frame_times_processed"] == 1
    assert manifest["frame_times_missing"] == 1
    assert manifest["missing_frame_times_sample"] == [SECOND_FRAME.isoformat()]
    assert manifest["row_count"] == 1  # only FIRST_FRAME's one instrument


async def test_never_overwrites_an_existing_dataset(session_factory, tmp_path):
    """A dataset is a permanent record: a second generation into the same
    directory always fails and leaves the original byte-for-byte intact."""
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_5m_range(session_factory, btc, FIRST_FRAME - timedelta(minutes=75), 90, 100)
    await _finalize_frame(session_factory, btc, FIRST_FRAME)

    output_dir = tmp_path / "research_v1"
    kwargs = dict(
        start=FIRST_FRAME,
        end=FIRST_FRAME + timedelta(minutes=15),
        interval_minutes=15,
        horizons=HORIZONS,
        output_dir=output_dir,
    )
    first = await generate_dataset(session_factory, **kwargs)

    with pytest.raises(FileExistsError):
        await generate_dataset(session_factory, **kwargs)

    assert file_sha256(output_dir / "data.parquet") == first["data_sha256"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["research_v1"]  # no stray tmp dir


async def test_a_failing_publish_check_publishes_nothing(session_factory, tmp_path):
    """`before_publish` (the generation lock check) runs after verification,
    right before the rename: if it raises, no dataset and no tmp dir remain."""
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_5m_range(session_factory, btc, FIRST_FRAME - timedelta(minutes=75), 90, 100)
    await _finalize_frame(session_factory, btc, FIRST_FRAME)

    async def lock_lost() -> None:
        raise RuntimeError("generation lock lost")

    with pytest.raises(RuntimeError, match="lock lost"):
        await generate_dataset(
            session_factory,
            start=FIRST_FRAME,
            end=FIRST_FRAME + timedelta(minutes=15),
            interval_minutes=15,
            horizons=HORIZONS,
            output_dir=tmp_path / "research_v1",
            before_publish=lock_lost,
        )
    assert list(tmp_path.iterdir()) == []


async def test_manifest_records_hash_provenance_and_actual_coverage(session_factory, tmp_path):
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_5m_range(session_factory, btc, FIRST_FRAME - timedelta(minutes=75), 90, 100)
    await _finalize_frame(session_factory, btc, FIRST_FRAME)

    output_dir = tmp_path / "v"
    manifest = await generate_dataset(
        session_factory,
        start=FIRST_FRAME - timedelta(hours=1),
        end=FIRST_FRAME + timedelta(minutes=15),
        interval_minutes=15,
        horizons=HORIZONS,
        output_dir=output_dir,
    )

    assert manifest["data_sha256"] == file_sha256(output_dir / "data.parquet")
    assert manifest["code_fingerprint"] == code_fingerprint()
    assert manifest["first_frame_time"] == FIRST_FRAME.isoformat()
    assert manifest["last_frame_time"] == FIRST_FRAME.isoformat()
    assert manifest["distinct_utc_days"] == 1
    assert manifest["sample_interval_minutes"] == 15
    assert "regenerable" in manifest["source_retention_note"]


async def test_a_frame_whose_members_were_pruned_is_counted_not_processed(
    session_factory, tmp_path
):
    """Retention drops `market_frame_members` partitions while frame
    headers stay — replaying such a frame hydrates zero members. That must
    be reported as missing (pruned), never as a processed empty frame."""
    from sqlalchemy import delete

    from app.models.frame import MarketFrameMember

    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_5m_range(session_factory, btc, FIRST_FRAME - timedelta(minutes=75), 90, 100)
    await _finalize_frame(session_factory, btc, FIRST_FRAME)
    await _finalize_frame(session_factory, btc, SECOND_FRAME)
    async with session_factory() as session:
        await session.execute(
            delete(MarketFrameMember).where(MarketFrameMember.frame_time == SECOND_FRAME)
        )
        await session.commit()

    manifest = await generate_dataset(
        session_factory,
        start=FIRST_FRAME,
        end=SECOND_FRAME + timedelta(minutes=15),
        interval_minutes=15,
        horizons=HORIZONS,
        output_dir=tmp_path / "v",
    )

    assert manifest["frame_times_processed"] == 1
    assert manifest["frame_times_missing_pruned"] == 1
    assert manifest["row_count"] == 1


async def test_a_write_failure_never_leaves_a_partial_looking_output_dir(
    session_factory, tmp_path, monkeypatch
):
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_5m_range(session_factory, btc, FIRST_FRAME - timedelta(minutes=75), 90, 100)
    await _finalize_frame(session_factory, btc, FIRST_FRAME)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(dataset_module.pq, "write_table", _boom)

    output_dir = tmp_path / "research_v1"
    with pytest.raises(RuntimeError, match="simulated write failure"):
        await generate_dataset(
            session_factory,
            start=FIRST_FRAME,
            end=FIRST_FRAME + timedelta(minutes=15),
            interval_minutes=15,
            horizons=HORIZONS,
            output_dir=output_dir,
        )

    assert not output_dir.exists()
    # No stray `.research_v1.tmp-*` directory left behind either.
    assert list(tmp_path.iterdir()) == []


async def test_legacy_off_boundary_frames_are_never_sampled(session_factory, tmp_path):
    """Frames persisted before 5m became the base timeframe exist at every
    minute; only the 5m-boundary ones may enter a dataset."""
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_5m_range(session_factory, btc, FIRST_FRAME - timedelta(minutes=75), 90, 100)
    for minute in range(10):
        await _finalize_frame(session_factory, btc, FIRST_FRAME + timedelta(minutes=minute))

    manifest = await generate_dataset(
        session_factory,
        start=FIRST_FRAME,
        end=FIRST_FRAME + timedelta(minutes=10),
        interval_minutes=5,
        horizons=HORIZONS,
        output_dir=tmp_path / "v",
    )

    assert manifest["frame_times_candidate"] == 2  # 15:00, 15:05 — never 15:01..15:04
    table = pq.read_table(tmp_path / "v" / "data.parquet")
    assert sorted(t.minute for t in table.column("frame_time").to_pylist()) == [0, 5]


@pytest.mark.parametrize(
    ("interval", "horizons"),
    [(7, HORIZONS), (15, {"7m": timedelta(minutes=7)}), (15, {"90s": timedelta(seconds=90)})],
)
async def test_invalid_interval_or_horizon_is_rejected_before_writing_anything(
    session_factory, tmp_path, interval, horizons
):
    with pytest.raises(ValueError, match="5"):
        await generate_dataset(
            session_factory,
            start=FIRST_FRAME,
            end=FIRST_FRAME + timedelta(hours=1),
            interval_minutes=interval,
            horizons=horizons,
            output_dir=tmp_path / "v",
        )
    assert list(tmp_path.iterdir()) == []


# -- dataset versioning ------------------------------------------------------------


async def _generate_one(session_factory, output_dir) -> None:
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_5m_range(session_factory, btc, FIRST_FRAME - timedelta(minutes=75), 90, 100)
    await _finalize_frame(session_factory, btc, FIRST_FRAME)
    await generate_dataset(
        session_factory,
        start=FIRST_FRAME,
        end=FIRST_FRAME + timedelta(minutes=15),
        interval_minutes=15,
        horizons=HORIZONS,
        output_dir=output_dir,
    )


async def test_version_is_recorded_in_parquet_metadata_and_read_back(session_factory, tmp_path):
    output_dir = tmp_path / "v2"
    await _generate_one(session_factory, output_dir)

    metadata = pq.read_schema(output_dir / "data.parquet").metadata
    assert (
        metadata[b"market_truffler.dataset_schema_version"] == str(DATASET_SCHEMA_VERSION).encode()
    )
    assert metadata[b"market_truffler.base_timeframe"] == b"5m"

    manifest, table = read_dataset(output_dir)
    assert manifest["dataset_schema_version"] == DATASET_SCHEMA_VERSION
    assert table.num_rows == 1


async def test_a_pre_5m_dataset_without_a_version_is_refused(session_factory, tmp_path):
    """A version-1 (1m-era) dataset carries no version at all — it must be
    refused, never silently read as if comparable."""
    output_dir = tmp_path / "v1"
    await _generate_one(session_factory, output_dir)
    manifest = json.loads((output_dir / "manifest.json").read_text())
    for key in ("dataset_schema_version", "base_timeframe", "label_definition"):
        del manifest[key]
    (output_dir / "manifest.json").chmod(0o644)
    (output_dir / "manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(IncompatibleDatasetError, match="regenerate"):
        read_dataset(output_dir)


async def test_a_manifest_and_parquet_from_different_versions_are_refused(
    session_factory, tmp_path
):
    output_dir = tmp_path / "mixed"
    await _generate_one(session_factory, output_dir)
    table = pq.read_table(output_dir / "data.parquet")
    (output_dir / "data.parquet").chmod(0o644)
    pq.write_table(table.replace_schema_metadata({}), output_dir / "data.parquet")
    # Re-pin the hash so this exercises the version check, not integrity.
    manifest = json.loads((output_dir / "manifest.json").read_text())
    manifest["data_sha256"] = file_sha256(output_dir / "data.parquet")
    (output_dir / "manifest.json").chmod(0o644)
    (output_dir / "manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(IncompatibleDatasetError, match="parquet"):
        read_dataset(output_dir)


# -- CLI argument validation ----------------------------------------------------------


def _parse(*extra: str):
    from app.scripts.generate_research_dataset import _build_parser

    return _build_parser().parse_args(
        ["--start", "2026-10-01T00:00:00Z", "--end", "2026-10-02T00:00:00Z", "--output", "x"]
        + list(extra)
    )


def test_cli_defaults_are_5m_compatible():
    args = _parse()
    assert args.sample_interval_minutes == 15
    assert args.horizons == {
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
        "24h": timedelta(hours=24),
    }


@pytest.mark.parametrize(
    "extra",
    [
        ("--sample-interval-minutes", "7"),
        ("--sample-interval-minutes", "0"),
        ("--horizons", "15m,7m"),
        ("--horizons", "1m"),
    ],
)
def test_cli_rejects_values_that_are_not_multiples_of_5m(extra, capsys):
    with pytest.raises(SystemExit):
        _parse(*extra)
    assert "5" in capsys.readouterr().err


async def test_a_modified_data_file_fails_integrity_verification(session_factory, tmp_path):
    output_dir = tmp_path / "v2"
    await _generate_one(session_factory, output_dir)
    data = output_dir / "data.parquet"
    data.chmod(0o644)  # read-only is only a secondary safeguard
    with data.open("ab") as handle:
        handle.write(b"\0")

    with pytest.raises(DatasetIntegrityError, match="sha256"):
        read_dataset(output_dir)


async def test_a_dataset_failing_verification_before_rename_leaves_nothing(
    session_factory, tmp_path, monkeypatch
):
    """Verification runs inside the temp directory: if it fails, no
    directory ever appears under the real name and no temp dir remains."""
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_5m_range(session_factory, btc, FIRST_FRAME - timedelta(minutes=75), 90, 100)
    await _finalize_frame(session_factory, btc, FIRST_FRAME)

    def _corrupt(*args, **kwargs):
        raise DatasetIntegrityError("simulated bad bytes")

    monkeypatch.setattr(dataset_module, "read_dataset", _corrupt)
    with pytest.raises(DatasetIntegrityError, match="simulated"):
        await generate_dataset(
            session_factory,
            start=FIRST_FRAME,
            end=FIRST_FRAME + timedelta(minutes=15),
            interval_minutes=15,
            horizons=HORIZONS,
            output_dir=tmp_path / "v",
        )
    assert list(tmp_path.iterdir()) == []


async def test_yield_time_and_caller_provenance_are_recorded(session_factory, tmp_path):
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_5m_range(session_factory, btc, FIRST_FRAME - timedelta(minutes=75), 90, 100)
    await _finalize_frame(session_factory, btc, FIRST_FRAME)
    await _finalize_frame(session_factory, btc, SECOND_FRAME)
    calls = []

    async def before_frame() -> float:
        calls.append(1)
        return 1.25

    manifest = await generate_dataset(
        session_factory,
        start=FIRST_FRAME,
        end=SECOND_FRAME + timedelta(minutes=15),
        interval_minutes=15,
        horizons=HORIZONS,
        output_dir=tmp_path / "v",
        before_frame=before_frame,
        extra_manifest={"generation_mode": "continuity"},
    )
    assert len(calls) == 2
    assert manifest["yield_seconds"] == 2.5
    assert manifest["generation_mode"] == "continuity"
    assert read_dataset(tmp_path / "v")[0] == manifest
