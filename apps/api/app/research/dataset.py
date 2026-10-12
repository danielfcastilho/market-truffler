"""OINK CORP's research dataset assembly: samples a wall-clock-aligned grid
of historical frame_times, replays Sniffs (`app.research.replay`), attaches
forward-outcome labels (`app.research.forward_returns`), and materializes
the result as a Parquet file plus a JSON manifest.

SAMPLING — deterministic, wall-clock-aligned, never "every Nth row that
happens to exist". The sampling interval must be a positive multiple of
the 5m base timeframe (5, 10, 15, ... minutes), so every grid point is a
5-minute boundary — the only instants MARKET builds frames at and the
only ones `app.research.replay` accepts. A naive "keep every Nth returned
frame_time" scheme silently drifts the whole sampling grid whenever a frame
is missing (a rare BUILDING-interrupted frame, or a `frame_time` whose
partition has already
been pruned) — frame #47 going missing would shift which frame becomes the
new "48th", with no way to tell from the output alone that the grid moved.
Instead, candidates are generated purely from absolute UTC time
(`aligned_candidate_times`, anchored to epoch-minute divisibility — NOT to
the requested `start`, so widening or shifting `--start`/`--end` never
changes which boundaries the grid lands on), and a candidate whose exact
`frame_time` doesn't exist is **skipped and counted**, never silently
replaced by a neighboring frame.

VERSIONING — `DATASET_SCHEMA_VERSION` is written into both `manifest.json`
and the Parquet file's own schema metadata, together with the base
timeframe and the label definition. Version 1 datasets (pre-5m: sampled on
any minute, labels from 1m candles) carry no version at all; `read_dataset`
refuses anything that isn't exactly the current version, so an old and a
new dataset can never be silently loaded side by side as if comparable.
Bump the version whenever a feature or label definition changes meaning.

RETENTION VISIBILITY — a successful run must never look like OINK CORP has
unlimited historical raw-data replay. Production retention (~30-61 days for
candles/frame members, 16 days for OI/Funding, no archival) is the real
ceiling on how far back full-feature replay can reach; this module doesn't
solve that (no second database, no archival, no production retention
change), but it does make the ceiling visible: the manifest records how
many aligned candidates existed vs. how many were actually replayable, and
the per-column non-null coverage across the whole dataset — a low coverage
number on `funding_rate_current` for an older range is that ceiling made
concrete, not a bug in this module.
"""

import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.frame import MarketFrameMember
from app.domain.sniffer import SnifferInstrumentResult
from app.domain.timeframes import BASE_DURATION, BASE_TIMEFRAME
from app.features.engine import FEATURES
from app.repositories.candle_repository import CandleRepository
from app.repositories.frame_repository import FrameRepository
from app.research.forward_returns import (
    compute_forward_relative_returns,
    compute_forward_returns,
    validate_horizons,
)
from app.research.replay import replay_frame

SessionFactory = Callable[[], AsyncSession]

_FEATURE_NAMES: list[str] = [feature.name for feature in FEATURES]

#: Bump whenever a column's meaning changes (see module docstring).
DATASET_SCHEMA_VERSION = 2
_BASE_MINUTES = int(BASE_DURATION.total_seconds() // 60)
_LABEL_DEFINITION = (
    "forward_return_H = P(T+H) / P(T) - 1, where P(X) is the close of the 5m candle "
    "closing exactly at X (open_time = X - 5m). Null when the frame's own 5m anchor "
    "isn't the candle closing at T, or the candle closing at T+H doesn't exist. "
    "forward_relative_return_H = forward_return_H - cross-sectional median of "
    "non-null forward_return_H in the same frame."
)
_PARQUET_METADATA_PREFIX = b"market_truffler."


class IncompatibleDatasetError(ValueError):
    """A dataset on disk was produced under a different schema/label
    definition than this code understands."""


def validate_sample_interval(interval_minutes: int) -> None:
    """Raise `ValueError` unless `interval_minutes` is a positive multiple
    of the 5m base timeframe."""
    if interval_minutes <= 0 or interval_minutes % _BASE_MINUTES != 0:
        raise ValueError(
            f"sample interval must be a positive multiple of {_BASE_MINUTES} minutes "
            f"(the {BASE_TIMEFRAME} base timeframe), got {interval_minutes}"
        )


def aligned_candidate_times(
    start: datetime, end: datetime, interval_minutes: int
) -> list[datetime]:
    """Every whole-minute UTC timestamp in `[start, end)` whose count of
    minutes since the Unix epoch is divisible by `interval_minutes` — a
    grid anchored to absolute time, not to `start`, so two calls with
    different `start`s but the same `interval_minutes` always agree on
    where the grid points are. `interval_minutes` must be a multiple of
    5, so every grid point is also a 5-minute boundary."""
    validate_sample_interval(interval_minutes)

    epoch_minute = int(start.timestamp() // 60)
    remainder = epoch_minute % interval_minutes
    first_offset = 0 if remainder == 0 else interval_minutes - remainder
    first = (start.replace(second=0, microsecond=0)) + timedelta(minutes=first_offset)
    # `start` itself may have seconds/microseconds; only advance to the next
    # aligned minute if `start` wasn't already exactly on one.
    if first < start:
        first += timedelta(minutes=interval_minutes)

    candidates = []
    current = first
    step = timedelta(minutes=interval_minutes)
    while current < end:
        candidates.append(current)
        current += step
    return candidates


def _get_git_commit() -> str | None:
    """Best-effort — `.git` isn't part of the `api` image's Docker build
    context (see `apps/api/Dockerfile`'s `COPY` list), so this is `None`
    when run via `docker compose exec`, and only resolves to a real commit
    when run from a host checkout with `.git` present."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


@dataclass
class _Columns:
    frame_time: list[datetime]
    instrument_id: list[int]
    symbol: list[str]
    features: dict[str, list[float | None]]
    forward_returns: dict[str, list[float | None]]
    forward_relative_returns: dict[str, list[float | None]]


def _empty_columns(horizon_labels: Sequence[str]) -> _Columns:
    return _Columns(
        frame_time=[],
        instrument_id=[],
        symbol=[],
        features={name: [] for name in _FEATURE_NAMES},
        forward_returns={f"forward_return_{label}": [] for label in horizon_labels},
        forward_relative_returns={
            f"forward_relative_return_{label}": [] for label in horizon_labels
        },
    )


def _append_row(
    columns: _Columns,
    frame_time: datetime,
    member: MarketFrameMember,
    sniff: SnifferInstrumentResult,
    forward: dict[str, Any],
    relative: dict[str, Any],
) -> None:
    columns.frame_time.append(frame_time)
    columns.instrument_id.append(member.instrument_id)
    columns.symbol.append(member.symbol)
    for name in _FEATURE_NAMES:
        value = sniff.features.get(name)
        columns.features[name].append(float(value) if value is not None else None)
    for key, values in columns.forward_returns.items():
        value = forward.get(key)
        values.append(float(value) if value is not None else None)
    for key, values in columns.forward_relative_returns.items():
        value = relative.get(key)
        values.append(float(value) if value is not None else None)


def _build_table(columns: _Columns) -> pa.Table:
    fields: list[tuple[str, pa.DataType]] = [
        ("frame_time", pa.timestamp("us", tz="UTC")),
        ("instrument_id", pa.int64()),
        ("symbol", pa.string()),
    ]
    data: dict[str, Any] = {
        "frame_time": columns.frame_time,
        "instrument_id": columns.instrument_id,
        "symbol": columns.symbol,
    }
    for name in _FEATURE_NAMES:
        fields.append((name, pa.float64()))
        data[name] = columns.features[name]
    for key, values in columns.forward_returns.items():
        fields.append((key, pa.float64()))
        data[key] = values
    for key, values in columns.forward_relative_returns.items():
        fields.append((key, pa.float64()))
        data[key] = values

    schema = pa.schema(
        fields,
        metadata={
            _PARQUET_METADATA_PREFIX + b"dataset_schema_version": str(
                DATASET_SCHEMA_VERSION
            ).encode(),
            _PARQUET_METADATA_PREFIX + b"base_timeframe": BASE_TIMEFRAME.encode(),
            _PARQUET_METADATA_PREFIX + b"label_definition": _LABEL_DEFINITION.encode(),
        },
    )
    return pa.table(data, schema=schema)


def _column_coverage(columns: _Columns, row_count: int) -> dict[str, float]:
    if row_count == 0:
        return {}
    coverage: dict[str, float] = {}
    for name, values in columns.features.items():
        coverage[name] = sum(1 for v in values if v is not None) / row_count
    for name, values in {**columns.forward_returns, **columns.forward_relative_returns}.items():
        coverage[name] = sum(1 for v in values if v is not None) / row_count
    return coverage


async def generate_dataset(
    session_factory: SessionFactory,
    *,
    start: datetime,
    end: datetime,
    interval_minutes: int,
    horizons: dict[str, timedelta],
    output_dir: Path,
    force: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Generate one research dataset covering `[start, end)` and write it to
    `output_dir` (Parquet + `manifest.json`). Returns the manifest dict.

    Writes to a sibling temporary directory first and only `rename`s it
    into place as the last step (atomic on the same filesystem) — a failed
    or interrupted run never leaves a directory at `output_dir` that looks
    like a completed dataset. Refuses to overwrite an existing `output_dir`
    unless `force=True`.

    Raises `ValueError` for a sample interval or horizon that isn't a
    multiple of 5 minutes, before touching anything on disk.
    """
    validate_sample_interval(interval_minutes)
    validate_horizons(horizons)
    if output_dir.exists():
        if not force:
            raise FileExistsError(
                f"{output_dir} already exists — pass force=True/--force to overwrite"
            )
        shutil.rmtree(output_dir)

    horizon_labels = list(horizons)
    candidates = aligned_candidate_times(start, end, interval_minutes)

    async with session_factory() as session:
        existing = set(await FrameRepository(session).list_frame_times_in_range(start, end))

    sampled = [t for t in candidates if t in existing]
    missing = [t for t in candidates if t not in existing]

    columns = _empty_columns(horizon_labels)
    processed = 0
    for frame_time in sampled:
        async with session_factory() as session:
            replay = await replay_frame(session, frame_time)
            if replay is None:
                # Genuinely rare: existed a moment ago (`existing`), but
                # e.g. its partition was concurrently pruned mid-run.
                missing.append(frame_time)
                continue
            candle_repo = CandleRepository(session)
            forward = await compute_forward_returns(replay.frame, candle_repo, horizons)

        relative = compute_forward_relative_returns(forward, horizon_labels)
        sniffs_by_id = {i.instrument_id: i for i in replay.sniffs.instruments}
        for member in replay.frame.members:
            _append_row(
                columns,
                frame_time,
                member,
                sniffs_by_id[member.instrument_id],
                forward[member.instrument_id],
                relative[member.instrument_id],
            )
        processed += 1
        if on_progress is not None:
            on_progress(processed, len(sampled))

    row_count = len(columns.instrument_id)
    table = _build_table(columns)
    coverage = _column_coverage(columns, row_count)

    manifest: dict[str, Any] = {
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "base_timeframe": BASE_TIMEFRAME,
        "label_definition": _LABEL_DEFINITION,
        "generated_at": datetime.now(UTC).isoformat(),
        "git_commit": _get_git_commit(),
        "feature_names": _FEATURE_NAMES,
        "horizons_minutes": {
            label: int(horizon.total_seconds() // 60) for label, horizon in horizons.items()
        },
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "sample_interval_minutes": interval_minutes,
        "row_count": row_count,
        "instrument_count": len(set(columns.instrument_id)),
        "frame_times_candidate": len(candidates),
        "frame_times_processed": processed,
        "frame_times_missing": len(missing),
        "missing_frame_times_sample": [t.isoformat() for t in missing[:20]],
        "column_coverage": coverage,
        "universe_note": (
            "Each row's cross-sectional universe is exactly that frame's own "
            "persisted market_frame_members — never the currently-active "
            "instrument set."
        ),
        "replay_note": (
            "Sniffs are recomputed with today's FeatureEngine/FEATURES against "
            "data knowable at frame_time — this is 'what current Sniffer would "
            "measure at T', not a replay of whatever software version actually "
            "ran at T. See app.research.replay's module docstring."
        ),
    }

    tmp_dir = output_dir.parent / f".{output_dir.name}.tmp-{uuid4().hex[:8]}"
    tmp_dir.mkdir(parents=True, exist_ok=False)
    try:
        pq.write_table(table, tmp_dir / "data.parquet")
        (tmp_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir.rename(output_dir)
    return manifest


def read_dataset(dataset_dir: Path) -> tuple[dict[str, Any], pa.Table]:
    """Load a dataset written by `generate_dataset` as `(manifest, table)`,
    refusing (`IncompatibleDatasetError`) anything not produced under the
    current `DATASET_SCHEMA_VERSION` and base timeframe — checked against
    both the manifest and the Parquet file's own metadata, so a manifest
    and data file from different generations can't be mixed either."""
    manifest = json.loads((dataset_dir / "manifest.json").read_text())
    table = pq.read_table(dataset_dir / "data.parquet")
    metadata = table.schema.metadata or {}

    found = {
        "manifest dataset_schema_version": manifest.get("dataset_schema_version"),
        "manifest base_timeframe": manifest.get("base_timeframe"),
        "parquet dataset_schema_version": metadata.get(
            _PARQUET_METADATA_PREFIX + b"dataset_schema_version", b""
        ).decode()
        or None,
        "parquet base_timeframe": metadata.get(
            _PARQUET_METADATA_PREFIX + b"base_timeframe", b""
        ).decode()
        or None,
    }
    expected = {
        "manifest dataset_schema_version": DATASET_SCHEMA_VERSION,
        "manifest base_timeframe": BASE_TIMEFRAME,
        "parquet dataset_schema_version": str(DATASET_SCHEMA_VERSION),
        "parquet base_timeframe": BASE_TIMEFRAME,
    }
    mismatches = {k: v for k, v in found.items() if v != expected[k]}
    if mismatches:
        raise IncompatibleDatasetError(
            f"{dataset_dir} is not a schema-v{DATASET_SCHEMA_VERSION}/{BASE_TIMEFRAME} dataset "
            f"(found {mismatches}); regenerate it with the current code"
        )
    return manifest, table
