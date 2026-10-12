"""Synthetic research datasets for OINK CORP experiment tests.

They exercise mechanics only (schema, hashes, selection, evaluation
arithmetic) — never evidence about markets or trading performance.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from app.features.engine import FEATURES
from app.research import dataset as dataset_module

FEATURE_NAMES = [f.name for f in FEATURES]
LABELS = ["forward_return_1h", "forward_relative_return_1h"]


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC)


def write_dataset(
    directory: Path,
    rows: list[dict[str, Any]],
    *,
    sample_interval_minutes: int = 5,
    horizons_minutes: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Write a schema-v2 dataset (Parquet + manifest with data_sha256).
    Each row needs `frame_time` and `symbol`; any feature/label it omits is
    null."""
    horizons = horizons_minutes or {"1h": 60}
    labels = [f"forward_return_{h}" for h in horizons] + [
        f"forward_relative_return_{h}" for h in horizons
    ]
    symbols = sorted({r["symbol"] for r in rows})
    ids = {s: i + 1 for i, s in enumerate(symbols)}
    fields = [
        ("frame_time", pa.timestamp("us", tz="UTC")),
        ("instrument_id", pa.int64()),
        ("symbol", pa.string()),
        *((name, pa.float64()) for name in FEATURE_NAMES + labels),
    ]
    data = {
        "frame_time": [r["frame_time"] for r in rows],
        "instrument_id": [ids[r["symbol"]] for r in rows],
        "symbol": [r["symbol"] for r in rows],
        **{name: [r.get(name) for r in rows] for name in FEATURE_NAMES + labels},
    }
    prefix = dataset_module._PARQUET_METADATA_PREFIX
    schema = pa.schema(
        fields,
        metadata={
            prefix + b"dataset_schema_version": str(dataset_module.DATASET_SCHEMA_VERSION).encode(),
            prefix + b"base_timeframe": b"5m",
        },
    )
    directory.mkdir(parents=True)
    pq.write_table(pa.table(data, schema=schema), directory / "data.parquet")
    times = [r["frame_time"] for r in rows]
    manifest = {
        "dataset_schema_version": dataset_module.DATASET_SCHEMA_VERSION,
        "base_timeframe": "5m",
        "feature_names": FEATURE_NAMES,
        "horizons_minutes": horizons,
        "sample_interval_minutes": sample_interval_minutes,
        "first_frame_time": min(times).isoformat() if times else None,
        "last_frame_time": max(times).isoformat() if times else None,
        "code_fingerprint": "test",
        "data_sha256": dataset_module.file_sha256(directory / "data.parquet"),
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return manifest


def universe_rows(
    frame_time: datetime, n: int, *, feature: str = "return_24h", label: str | None = None
) -> list[dict[str, Any]]:
    """n symbols at one timestamp: symbol k has feature value k (so its
    percentile is (k + 1) / n) and, by default, label value k / 1000."""
    label = label or "forward_relative_return_1h"
    return [
        {"frame_time": frame_time, "symbol": f"S{k:03d}", feature: float(k), label: k / 1000}
        for k in range(n)
    ]


def spec_dict(dataset_name: str, data_sha256: str, **overrides: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "spec_version": 1,
        "experiment_id": "test-experiment-v1",
        "heuristic": {"id": "test-heuristic", "text": "A test intuition."},
        "hypothesis": {
            "statement": "Bottom decile by return_24h.",
            "direction": "long",
            "conditions": [
                {"feature": "return_24h", "transform": "cs_percentile", "op": "<=", "value": 0.1}
            ],
        },
        "datasets": [{"name": dataset_name, "data_sha256": data_sha256}],
        "label": "forward_relative_return_1h",
    }
    spec.update(overrides)
    return spec


def daily_grid(day: int, count: int, interval_minutes: int = 5) -> list[datetime]:
    start = at(day)
    return [start + timedelta(minutes=interval_minutes * i) for i in range(count)]
