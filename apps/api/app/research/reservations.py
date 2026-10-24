"""Reserved evaluation data: the access boundary of the prospective
evaluation protocol (`app.research.prospective`).

A registered prospective evaluation reserves future UTC-day partitions
(validation, protected holdout) under `<data_dir>/evaluations/`, next to the
data they protect — so the policy can't be lost by pointing a command at
another directory. Until a partition's evaluation has completed, no dataset
whose outcome window (first frame time to last frame time plus its longest
label horizon) touches it may be loaded by research code, except by the
protected evaluation itself holding an access grant for a *started*
attempt — recorded before any label is read.

`app.research.experiment.load_datasets`, the only research path that reads
outcome labels, calls `check_access` before reading any Parquet file, and
fails closed: an unreadable registration makes every reservation unknown,
so nothing is loaded. Integrity tooling (hash verification, backup) reads
dataset bytes but never evaluates them. This is a scientific workflow
boundary, not protection against someone with filesystem access: pyarrow or
`read_dataset` called directly bypasses it.
"""

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

EVALUATIONS_DIRNAME = "evaluations"


class ReservedDataError(ValueError):
    """A dataset overlaps data reserved for a prospective evaluation."""


@dataclass(frozen=True)
class Access:
    """Grant for one started attempt of one registration's partition."""

    registration_id: str
    role: str
    attempt_id: str


@dataclass(frozen=True)
class Reservation:
    registration_id: str
    role: str
    start: datetime
    end: datetime  # exclusive


def day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def evaluations_root(data_dir: Path) -> Path:
    return data_dir / EVALUATIONS_DIRNAME


def read_registration_file(directory: Path) -> dict[str, Any]:
    path = directory / "registration" / "registration.json"
    try:
        stored = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ReservedDataError(
            f"registration {directory.name} is unreadable ({exc}); reservations are unknown, "
            "so no dataset may be loaded"
        ) from exc
    if not isinstance(stored, dict) or "registration" not in stored:
        raise ReservedDataError(f"registration {directory.name} is malformed")
    return stored


def read_events(directory: Path) -> list[dict[str, Any]]:
    events = []
    for path in sorted((directory / "events").glob("[0-9][0-9][0-9][0-9][0-9][0-9]/event.json")):
        try:
            events.append(json.loads(path.read_text()))
        except (OSError, ValueError) as exc:
            raise ReservedDataError(f"exposure event {path} is unreadable ({exc})") from exc
    return events


def registration_dirs(data_dir: Path) -> list[Path]:
    root = evaluations_root(data_dir)
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def reservations(data_dir: Path) -> list[Reservation]:
    """Partitions whose evaluation has not completed (a started attempt
    that never completed keeps its partition reserved)."""
    reserved = []
    for directory in registration_dirs(data_dir):
        registration = read_registration_file(directory)["registration"]
        completed = {e.get("role") for e in read_events(directory) if e.get("type") == "completed"}
        for partition in registration.get("partitions", []):
            if partition["role"] in completed:
                continue
            reserved.append(
                Reservation(
                    registration_id=registration["registration_id"],
                    role=partition["role"],
                    start=day_start(date.fromisoformat(partition["start"])),
                    end=day_start(date.fromisoformat(partition["end"])),
                )
            )
    return reserved


def outcome_window(manifest: dict[str, Any]) -> tuple[datetime, datetime] | None:
    """[first frame time, last frame time + longest label horizon] — every
    instant whose prices the dataset's features or labels depend on, from
    the first decision on. None for an empty dataset."""
    first, last = manifest.get("first_frame_time"), manifest.get("last_frame_time")
    if not first or not last:
        return None
    horizon = max((manifest.get("horizons_minutes") or {"_": 0}).values())
    return (
        datetime.fromisoformat(first),
        datetime.fromisoformat(last) + timedelta(minutes=horizon),
    )


def _grant_is_live(data_dir: Path, access: Access) -> bool:
    events = read_events(evaluations_root(data_dir) / access.registration_id)
    attempt = [e for e in events if e.get("attempt_id") == access.attempt_id]
    return any(
        e.get("type") == "started" and e.get("role") == access.role for e in attempt
    ) and not any(e.get("type") in ("completed", "failed") for e in attempt)


def check_access(
    data_dir: Path, manifests: list[tuple[str, dict[str, Any]]], access: Access | None
) -> None:
    """Refuse (`ReservedDataError`) to load datasets reaching into reserved
    data, unless `access` is a live grant for exactly that reservation."""
    reserved = reservations(data_dir)
    if not reserved:
        return
    live = access is not None and _grant_is_live(data_dir, access)
    for name, manifest in manifests:
        window = outcome_window(manifest)
        if window is None:
            continue
        for r in reserved:
            if window[0] < r.end and r.start <= window[1]:
                granted = (
                    live
                    and access is not None
                    and (access.registration_id, access.role) == (r.registration_id, r.role)
                )
                if not granted:
                    raise ReservedDataError(
                        f"dataset {name} reaches into data reserved for the {r.role} of "
                        f"prospective evaluation {r.registration_id} "
                        f"({r.start.date()} to {r.end.date()}, exclusive); it can only be read "
                        "by that evaluation (python -m app.scripts.prospective_evaluation)"
                    )


def check_generation_window(data_dir: Path, start: datetime, outcome_end: datetime) -> None:
    """Refuse (`ReservedDataError`) an ad-hoc dataset generation whose
    outcome window `[start, outcome_end]` touches reserved data — computing
    the labels is itself reading the protected outcomes (audit F-04). Only
    the continuity worker produces reserved days' shards, the preservation
    path a registration's dataset rule names; it does not call this."""
    for r in reservations(data_dir):
        if start < r.end and r.start <= outcome_end:
            raise ReservedDataError(
                f"[{start.isoformat()}, {outcome_end.isoformat()}] reaches into data reserved "
                f"for the {r.role} of prospective evaluation {r.registration_id} "
                f"({r.start.date()} to {r.end.date()}, exclusive); its datasets are produced "
                "only by the continuity worker and read only by that evaluation"
            )
