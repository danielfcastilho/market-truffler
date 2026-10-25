"""Research-only historical reconstruction (`app.research.reconstruction`)."""

import argparse
import json
from datetime import timedelta
from pathlib import Path

import pytest
from research_helpers import write_dataset
from sqlalchemy import func, select
from test_dataset import (
    FIRST_FRAME,
    HORIZONS,
    _add_instrument,
    _finalize_frame,
    _seed_5m_range,
    session_factory,  # noqa: F401 - fixture
)
from test_ranking import _lab

from app.models.frame import MarketFrame as MarketFrameRow
from app.models.frame import MarketFrameMember as MarketFrameMemberRow
from app.models.sniffer import SnifferResult
from app.research import ranking
from app.research.dataset import generate_dataset, read_dataset
from app.research.experiment import (
    RECONSTRUCTED_DISCLOSURE,
    DatasetRef,
    ExperimentSpecError,
    load_columns,
)
from app.research.reconstruction import RECONSTRUCTION_VERSION, reconstruct_frame
from app.research.replay import replay_frame
from app.research.reservations import Access, ReservedDataError
from app.scripts import generate_research_dataset as cli


def _members(frame):
    return {
        m.instrument_id: {
            tf: (getattr(m, tf).open_time, getattr(m, tf).close) if getattr(m, tf) else None
            for tf in ("m5", "m15", "h1", "h4", "h24")
        }
        for m in frame.members
    }


async def test_reconstruction_matches_the_live_frame_when_live_data_was_complete(
    session_factory,  # noqa: F811
):
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_5m_range(session_factory, btc, FIRST_FRAME - timedelta(minutes=75), 90, 100)
    await _finalize_frame(session_factory, btc, FIRST_FRAME)

    async with session_factory() as session:
        live = await replay_frame(session, FIRST_FRAME)
        rebuilt = await reconstruct_frame(session, FIRST_FRAME)
    assert live is not None
    assert _members(rebuilt.result.frame) == _members(live.frame)
    assert [s.features for s in rebuilt.result.sniffs.instruments] == [
        s.features for s in live.sniffs.instruments
    ]


async def test_the_universe_is_whoever_was_trading_at_t(session_factory):  # noqa: F811
    listed = await _add_instrument(session_factory, "BTCUSDT")
    delisted = await _add_instrument(session_factory, "OLDUSDT")
    later = await _add_instrument(session_factory, "NEWUSDT")
    start = FIRST_FRAME - timedelta(minutes=75)
    await _seed_5m_range(session_factory, listed, start, 150, 100)
    await _seed_5m_range(session_factory, delisted, start, 60, 100)  # stops at 14:45
    await _seed_5m_range(session_factory, later, FIRST_FRAME, 75, 100)  # lists at 15:00

    async with session_factory() as session:
        at_first = await reconstruct_frame(session, FIRST_FRAME)
        # NEWUSDT trades from 15:00 but has no closed 1h candle until 16:00.
        at_one_hour = await reconstruct_frame(session, FIRST_FRAME + timedelta(hours=1))
    assert {m.symbol for m in at_first.result.frame.members} == {"BTCUSDT"}
    assert at_first.trading == 1
    assert {m.symbol for m in at_one_hour.result.frame.members} == {"BTCUSDT", "NEWUSDT"}


async def test_a_reconstructed_dataset_never_writes_frames_and_records_provenance(
    session_factory,  # noqa: F811
    tmp_path,
):
    btc = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_5m_range(session_factory, btc, FIRST_FRAME - timedelta(minutes=75), 90, 100)

    manifest = await generate_dataset(
        session_factory,
        start=FIRST_FRAME,
        end=FIRST_FRAME + timedelta(minutes=15),
        interval_minutes=15,
        horizons=HORIZONS,
        output_dir=tmp_path / "recon_test",
        reconstruct=True,
    )
    async with session_factory() as session:
        for model in (MarketFrameRow, MarketFrameMemberRow, SnifferResult):
            assert await session.scalar(select(func.count()).select_from(model)) == 0
    assert manifest["observation_source"] == "reconstructed"
    assert manifest["reconstruction"]["version"] == RECONSTRUCTION_VERSION
    assert manifest["reconstruction"]["universe_size"] == {"min": 1, "max": 1, "mean": 1.0}
    assert "survivorship" in manifest["universe_note"]
    assert manifest["frame_times_processed"] == 1 and manifest["row_count"] == 1
    on_disk, table = read_dataset(tmp_path / "recon_test")  # hash and versions verify
    assert on_disk == manifest and table.num_rows == 1


async def test_a_grid_point_with_nobody_trading_is_missing_not_empty(
    session_factory,  # noqa: F811
    tmp_path,
):
    manifest = await generate_dataset(
        session_factory,
        start=FIRST_FRAME,
        end=FIRST_FRAME + timedelta(minutes=15),
        interval_minutes=15,
        horizons=HORIZONS,
        output_dir=tmp_path / "recon_empty",
        reconstruct=True,
    )
    assert manifest["frame_times_processed"] == 0 and manifest["frame_times_missing"] == 1


# -- loading: never pooled, never prospective ---------------------------------------------------


def _dataset(data: Path, name: str, rows, *, reconstructed: bool) -> DatasetRef:
    manifest = write_dataset(data / name, rows, sample_interval_minutes=15)
    if reconstructed:
        manifest["observation_source"] = "reconstructed"
        (data / name / "manifest.json").write_text(json.dumps(manifest))
    return DatasetRef(name=name, data_sha256=manifest["data_sha256"])


def _rows(day: int):
    t = FIRST_FRAME + timedelta(days=day)
    return [{"frame_time": t, "symbol": "BTCUSDT", "return_1h": 0.01}]


def test_live_and_reconstructed_datasets_are_never_pooled(tmp_path):
    live = _dataset(tmp_path, "live", _rows(0), reconstructed=False)
    recon = _dataset(tmp_path, "recon_x", _rows(1), reconstructed=True)
    with pytest.raises(ExperimentSpecError, match="mix live-observed and reconstructed"):
        load_columns([live, recon], ["return_1h"], "forward_relative_return_1h", tmp_path)
    loaded = load_columns([recon], ["return_1h"], "forward_relative_return_1h", tmp_path)
    assert loaded.observation_source == "reconstructed"


def test_a_prospective_evaluation_never_reads_reconstructed_data(tmp_path):
    recon = _dataset(tmp_path, "recon_x", _rows(0), reconstructed=True)
    access = Access(registration_id="r", role="validation", attempt_id="a")
    with pytest.raises(ExperimentSpecError, match="exploratory only"):
        load_columns([recon], ["return_1h"], "forward_relative_return_1h", tmp_path, access=access)


def _reserve(data_dir: Path, start: str, end: str) -> None:
    reg = data_dir / "evaluations" / "test-reg-v1" / "registration"
    reg.mkdir(parents=True)
    (reg / "registration.json").write_text(
        json.dumps(
            {
                "registration": {
                    "registration_id": "test-reg-v1",
                    "partitions": [{"role": "validation", "start": start, "end": end}],
                }
            }
        )
    )


def test_reconstructed_data_in_a_reserved_period_stays_refused(tmp_path):
    recon = _dataset(tmp_path, "recon_x", _rows(0), reconstructed=True)
    day = FIRST_FRAME.date()
    _reserve(tmp_path, day.isoformat(), (day + timedelta(days=1)).isoformat())
    with pytest.raises(ExperimentSpecError, match="reserved for the validation"):
        load_columns([recon], ["return_1h"], "forward_relative_return_1h", tmp_path)


async def test_the_cli_refuses_reserved_windows_and_unmarked_names(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_DATA_DIR", str(tmp_path))
    _reserve(tmp_path, "2026-10-26", "2026-11-25")

    def args(output: str, start: str, end: str) -> argparse.Namespace:
        return cli._build_parser().parse_args(
            ["--reconstruct", "--start", start, "--end", end, "--output", output]
        )

    with pytest.raises(RuntimeError, match="recon_"):
        await cli._run(args("research_x", "2026-10-20T00:00:00Z", "2026-10-21T00:00:00Z"))
    with pytest.raises(ReservedDataError):
        # Its 24h labels reach into the reservation.
        await cli._run(args("recon_x", "2026-10-25T00:00:00Z", "2026-10-26T00:00:00Z"))
    plain = cli._build_parser().parse_args(
        ["--start", "2026-10-20T00:00:00Z", "--end", "2026-10-21T00:00:00Z", "--output", "recon_x"]
    )
    with pytest.raises(RuntimeError, match="recon_"):
        await cli._run(plain)
    assert not any(p.name.startswith("recon_") for p in tmp_path.iterdir())


def test_ranking_studies_disclose_reconstructed_observations(tmp_path):
    path, data = _lab(tmp_path, days=3)
    manifest_path = data / "dev" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["observation_source"] = "reconstructed"
    manifest_path.write_text(json.dumps(manifest))
    study, cand, ref = ranking.load_study(path, path.parent.parent / "candidates")
    report, _ = ranking.evaluate_study(study, cand, ref, data, seed=7)
    assert report["observation_source"] == "reconstructed"
    assert RECONSTRUCTED_DISCLOSURE in report["disclosures"]
