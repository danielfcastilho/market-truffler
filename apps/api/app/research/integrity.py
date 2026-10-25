"""Scheduled integrity verification of research datasets, run by the
research worker (never on a request).

It reuses the existing verifiers and adds no new algorithm:
- **full** (`read_dataset(path, columns=["frame_time"])` plus the row count):
  sha256 of the whole data file against the manifest, the dataset schema and
  base-timeframe metadata of both manifest and Parquet file, and the number
  of rows — loading only the `frame_time` column, never a label;
- **content_hash_only** (`file_sha256` against the manifest's
  `data_sha256`) for a dataset whose outcome window reaches into a period
  reserved for a prospective evaluation (`reservations.check_access`
  refuses it): the bytes are hashed, the Parquet file is never parsed, so
  the protected outcomes are never read. Its result is `restricted`, never
  `verified`: a partial check is not reported as a full one.

Results are operational state in `continuity/verification.json` — never
written into a manifest, never touching a dataset, never scientific
evidence. Each record carries the manifest's own sha256: if the manifest
changes, the record no longer applies (status `pending`) rather than
vouching for different bytes. Records are written one dataset at a time,
so an interrupted pass loses at most the dataset it was checking.

Bounded: at most `max_per_pass` datasets per worker pass, least recently
checked first, each only once per `interval`.
"""

import hashlib
import json
import os
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.research.dataset import file_sha256, read_dataset
from app.research.reservations import ReservedDataError, check_access

VERIFICATION_FILE = "verification.json"
_NOT_DATASETS = {"continuity", "evaluations", "runs"}


def dataset_dirs(data_dir: Path) -> list[Path]:
    if not data_dir.is_dir():
        return []
    return [
        p
        for p in sorted(data_dir.iterdir())
        if p.is_dir() and not p.name.startswith(".") and p.name not in _NOT_DATASETS
    ]


def load_records(continuity_dir: Path) -> dict[str, dict[str, Any]]:
    try:
        value = json.loads((continuity_dir / VERIFICATION_FILE).read_text())
    except (OSError, ValueError):
        return {}
    records = value.get("datasets") if isinstance(value, dict) else None
    return records if isinstance(records, dict) else {}


def _save(continuity_dir: Path, records: dict[str, dict[str, Any]]) -> None:
    continuity_dir.mkdir(parents=True, exist_ok=True)
    path = continuity_dir / VERIFICATION_FILE
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps({"schema": 1, "datasets": records}, indent=2, sort_keys=True))
    tmp.replace(path)


def _manifest(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        raw = (path / "manifest.json").read_bytes()
        value = json.loads(raw)
    except (OSError, ValueError):
        return None, None
    return (value, hashlib.sha256(raw).hexdigest()) if isinstance(value, dict) else (None, None)


def verify_dataset(data_dir: Path, path: Path, now: datetime) -> dict[str, Any]:
    """One dataset's verification record. Never raises for a dataset
    problem: it becomes a `failed` record with the reason."""
    manifest, manifest_sha = _manifest(path)
    base: dict[str, Any] = {"checked_at": now.isoformat(), "manifest_sha256": manifest_sha}
    if manifest is None:
        return {
            **base,
            "result": "failed",
            "scope": "manifest",
            "error": "manifest missing or unreadable",
        }
    base["data_sha256"] = manifest.get("data_sha256")
    try:
        check_access(data_dir, [(path.name, manifest)], None)
        restricted = False
    except ReservedDataError:
        restricted = True  # reserved, or reservations unreadable: fail closed
    if not (path / "data.parquet").is_file():
        return {**base, "result": "failed", "scope": "file", "error": "data file missing"}
    if restricted:
        try:
            actual = file_sha256(path / "data.parquet")
        except OSError as exc:
            return {**base, "result": "failed", "scope": "content_hash_only", "error": str(exc)}
        if actual != manifest.get("data_sha256"):
            return {
                **base,
                "result": "failed",
                "scope": "content_hash_only",
                "error": f"data sha256 {actual} does not match the manifest",
            }
        return {
            **base,
            "result": "restricted",
            "scope": "content_hash_only",
            "note": "reaches into a reserved prospective period: bytes hashed, file not parsed",
        }
    try:
        _, table = read_dataset(path, columns=["frame_time"])
    except (OSError, ValueError) as exc:  # integrity and compatibility errors are ValueErrors
        return {**base, "result": "failed", "scope": "full", "error": str(exc)}
    if manifest.get("row_count") != table.num_rows:
        return {
            **base,
            "result": "failed",
            "scope": "full",
            "error": f"{table.num_rows} rows, manifest records {manifest.get('row_count')}",
        }
    return {**base, "result": "verified", "scope": "full"}


def due(
    data_dir: Path, records: dict[str, dict[str, Any]], now: datetime, interval: timedelta
) -> list[Path]:
    """Datasets due for a check, least recently checked first: never
    checked, manifest changed since the check, or checked longer ago than
    `interval`."""
    pending = []
    for path in dataset_dirs(data_dir):
        record = records.get(path.name)
        _, manifest_sha = _manifest(path)
        if (
            record is None
            or record.get("manifest_sha256") != manifest_sha
            or now - datetime.fromisoformat(record["checked_at"]) >= interval
        ):
            pending.append((record["checked_at"] if record else "", path.name, path))
    return [p for _, _, p in sorted(pending)]


def run_verification(
    data_dir: Path,
    continuity_dir: Path,
    *,
    now: datetime,
    interval: timedelta,
    max_per_pass: int,
    should_stop: Callable[[], bool] = lambda: False,
) -> list[str]:
    """Check up to `max_per_pass` due datasets, saving after each one.
    Returns the names checked. Records of datasets that no longer exist are
    dropped (their status is then simply absent, not "verified")."""
    records = load_records(continuity_dir)
    present = {p.name for p in dataset_dirs(data_dir)}
    records = {k: v for k, v in records.items() if k in present}
    checked = []
    for path in due(data_dir, records, now, interval)[:max_per_pass]:
        if should_stop():
            break
        records[path.name] = verify_dataset(data_dir, path, now)
        _save(continuity_dir, records)
        checked.append(path.name)
    if not checked:
        _save(continuity_dir, records)
    return checked


def verification_report(
    data_dir: Path, continuity_dir: Path, now: datetime, interval: timedelta
) -> dict[str, Any]:
    """Per dataset: `never_checked`, `pending` (manifest changed since its
    check), `verified`, `restricted`, `failed`, or `unavailable` (no
    readable manifest), with the record's time, scope and age — and whether
    it is overdue (not re-checked within twice the interval)."""
    records = load_records(continuity_dir)
    out: dict[str, dict[str, Any]] = {}
    for path in dataset_dirs(data_dir):
        manifest, manifest_sha = _manifest(path)
        record = records.get(path.name)
        if manifest is None:
            status = "unavailable"
        elif record is None:
            status = "never_checked"
        elif record.get("manifest_sha256") != manifest_sha:
            status = "pending"
        else:
            status = str(record["result"])
        entry: dict[str, Any] = {"status": status}
        if record is not None:
            age = now - datetime.fromisoformat(record["checked_at"])
            entry.update(
                {
                    "checked_at": record["checked_at"],
                    "scope": record.get("scope"),
                    "error": record.get("error"),
                    "overdue": age > 2 * interval,
                }
            )
        out[path.name] = entry
    counts: dict[str, int] = {}
    for entry in out.values():
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    checked = [e["checked_at"] for e in out.values() if e.get("checked_at")]
    return {
        "interval_hours": interval.total_seconds() / 3600,
        "last_checked_at": max(checked) if checked else None,
        "counts": dict(sorted(counts.items())),
        "datasets": out,
    }
