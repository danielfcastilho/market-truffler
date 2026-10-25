"""OINK CORP research data health: how much history exists, how complete it
is, which research shards exist and whether they are continuous, which
temporal-contract versions they carry, and how far each prospective
registration is from being evaluable. Read-only and descriptive: complete
data is not evidence of anything.

Sources, none of them new:
- frame headers and Sniffer result provenance (one read of
  `market_frames` with an index probe per frame, see
  `SnifferRepository.frame_health_rows`);
- shard manifests (`manifest.json` only — no Parquet file is opened, so no
  label is read, reserved or not);
- the research worker's own report (`continuity/status.json`): its per-day
  shard decisions and its registration readiness, computed there with the
  authoritative protocol functions (`app.research.readiness`). This module
  never re-derives eligibility or verification.

It imports no research code (`tests/test_architecture_boundaries.py`).

FRAME COVERAGE uses the canonical 5-minute grid over an explicit
observation interval: from the first on-grid frame ever recorded (nothing
before it is assumed) to the last boundary whose frame should have
finalized (`FINALIZE_ALLOWANCE`), so the frame being built is never missing.
Each grid slot is exactly one of: missing (no row), building (row never
finalized — incomplete), partial (finalized with fewer members than
expected), complete. Finalized frames with members are further analyzed,
unanalyzed, or analysis_unknown (older than the oldest retained Sniffer
results: pruned, not missing). Frames off the grid (from before 5m became
the base timeframe) are counted separately and never as coverage.
"""

import hashlib
import json
import re
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.timeframes import BASE_DURATION, floor_to_boundary
from app.repositories.sniffer_repository import SnifferRepository
from app.services.collection_continuity import FINALIZE_ALLOWANCE

SLOTS_PER_DAY = int(timedelta(days=1) / BASE_DURATION)
MAX_INTERVALS = 50
#: Requests within this many seconds reuse one computation.
CACHE_SECONDS = 60.0
_SHARD = re.compile(r"^shard_s15m_(\d{8})$")
_REQUIRED_MANIFEST = (
    "data_sha256",
    "first_frame_time",
    "last_frame_time",
    "row_count",
    "sample_interval_minutes",
    "horizons_minutes",
)
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


# -- frame coverage --------------------------------------------------------------------------


def _slot_class(status: str, expected: int, available: int) -> str:
    if status == "building":
        return "building"
    return "complete" if available >= expected else "partial"


def _intervals(
    slots: list[datetime], covered: list[bool]
) -> tuple[list[list[Any]], list[list[Any]]]:
    runs: dict[bool, list[list[Any]]] = {True: [], False: []}
    start = 0
    for i in range(1, len(slots) + 1):
        if i == len(slots) or covered[i] != covered[start]:
            runs[covered[start]].append(
                [slots[start].isoformat(), (slots[i - 1] + BASE_DURATION).isoformat(), i - start]
            )
            start = i
    return runs[True], runs[False]


def _bounded(intervals: list[list[Any]]) -> dict[str, Any]:
    """At most MAX_INTERVALS, the most recent; the total is always given."""
    return {
        "count": len(intervals),
        "truncated": len(intervals) > MAX_INTERVALS,
        "items": intervals[-MAX_INTERVALS:],
    }


def frame_coverage(
    rows: list[tuple[datetime, str, int, int, str | None]],
    now: datetime,
    results_from: datetime | None,
) -> dict[str, Any]:
    """Coverage of the 5-minute grid from the first on-grid frame to the
    last due boundary. `rows`: (frame_time, status, expected, available,
    analysis "v2" | "v1" | None), any order. Deterministic for a given
    input and `now`."""
    on_grid = {r[0]: r for r in rows if floor_to_boundary(r[0], BASE_DURATION) == r[0]}
    off_grid = len({r[0] for r in rows} - set(on_grid))
    # The table's primary key prevents this; reported, never double-counted.
    duplicates = len(rows) - len({r[0] for r in rows})
    end = floor_to_boundary(now - FINALIZE_ALLOWANCE, BASE_DURATION)
    due = sorted(t for t in on_grid if t <= end)
    if not due:
        return {
            "available": False,
            "reason": "no completed frame on the 5-minute grid yet",
            "off_grid_frames": off_grid,
            "duplicate_rows": duplicates,
        }
    start = due[0]
    slots = []
    t = start
    while t <= end:
        slots.append(t)
        t += BASE_DURATION

    totals = dict.fromkeys(
        (
            "missing",
            "building",
            "partial",
            "complete",
            "analyzed",
            "unanalyzed",
            "analysis_unknown",
        ),
        0,
    )
    contract: dict[str, list[datetime]] = {"v1": [], "v2": []}
    days: dict[date, dict[str, Any]] = {}
    covered = []
    for slot in slots:
        day = days.setdefault(
            slot.date(),
            {
                **dict.fromkeys(totals, 0),
                "expected": 0,
                "gap": 0,
                "largest_gap": 0,
                "v1": 0,
                "v2": 0,
            },
        )
        day["expected"] += 1
        row = on_grid.get(slot)
        cls = "missing" if row is None else _slot_class(row[1], row[2], row[3])
        totals[cls] += 1
        day[cls] += 1
        finalized = cls in ("complete", "partial")
        covered.append(finalized)
        day["gap"] = 0 if finalized else day["gap"] + 1
        day["largest_gap"] = max(day["largest_gap"], day["gap"])
        if finalized and row is not None and row[3] > 0:
            analysis = row[4]
            kind = (
                "analyzed"
                if analysis
                else "analysis_unknown"
                if results_from is None or slot < results_from
                else "unanalyzed"
            )
            totals[kind] += 1
            day[kind] += 1
            if analysis:
                contract[analysis].append(slot)
                day[analysis] += 1

    first_day, last_day = slots[0].date(), slots[-1].date()
    daily = []
    for d, v in sorted(days.items()):
        finalized = v["complete"] + v["partial"]
        # Only a day the observation interval cuts short is a partial day:
        # its missing slots outside the interval were never expected.
        boundary = None
        if v["expected"] < SLOTS_PER_DAY:
            boundary = (
                "first_and_last"
                if d == first_day == last_day
                else "first"
                if d == first_day
                else "last"
            )
        daily.append(
            {
                "day": d.isoformat(),
                "partial_day": boundary,
                "expected": v["expected"],
                "finalized": finalized,
                "complete": v["complete"],
                "partial": v["partial"],
                "building": v["building"],
                "missing": v["missing"],
                "analyzed": v["analyzed"],
                "unanalyzed": v["unanalyzed"],
                "analysis_unknown": v["analysis_unknown"],
                "coverage": finalized / v["expected"],
                "largest_gap_minutes": v["largest_gap"] * int(BASE_DURATION.total_seconds() // 60),
                "analyzed_by_contract": {"v1": v["v1"], "v2": v["v2"]},
            }
        )
    covered_runs, missing_runs = _intervals(slots, covered)
    finalized_total = totals["complete"] + totals["partial"]
    return {
        "available": True,
        "observed_from": start.isoformat(),
        "observed_to": (end + BASE_DURATION).isoformat(),
        "observed_note": "from the first on-grid frame ever recorded to the last boundary whose "
        "frame should have finalized; nothing before the first frame is assumed",
        "expected": len(slots),
        "finalized": finalized_total,
        **totals,
        "coverage": finalized_total / len(slots),
        "latest_frame": due[-1].isoformat(),
        "off_grid_frames": off_grid,
        "duplicate_rows": duplicates,
        "results_retained_from": results_from.isoformat() if results_from else None,
        "covered_intervals": _bounded(covered_runs),
        "missing_intervals": _bounded(missing_runs),
        "analyzed_by_contract": {
            version: {
                "frames": len(times),
                "first": times[0].isoformat() if times else None,
                "last": times[-1].isoformat() if times else None,
            }
            for version, times in contract.items()
        },
        "daily": daily,
    }


# -- research shards -----------------------------------------------------------------------


def _read(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError) as exc:
        return None, f"unreadable: {exc}"
    return (value, None) if isinstance(value, dict) else (None, "not a JSON object")


def _manifest_problems(name: str, manifest: dict[str, Any]) -> list[str]:
    problems = [f"no {field}" for field in _REQUIRED_MANIFEST if manifest.get(field) is None]
    match = _SHARD.match(name)
    if match:
        day = datetime.strptime(match.group(1), "%Y%m%d").date()
        if manifest.get("shard_day") not in (None, day.isoformat()):
            problems.append(f"shard_day {manifest.get('shard_day')} differs from its name")
        for field in ("first_frame_time", "last_frame_time"):
            value = manifest.get(field)
            try:
                if value and datetime.fromisoformat(value).date() != day:
                    problems.append(f"{field} {value} is outside {day}")
            except ValueError:
                problems.append(f"{field} is not a timestamp")
    return problems


def shard_inventory(data_dir: Path, worker: dict[str, Any] | None) -> dict[str, Any]:
    """Every dataset directory under the research data directory, from its
    manifest alone. `verification` is the worker's last explicit
    re-verification of that dataset when it ran one, else "not_checked" —
    a manifest that parses is never called verified."""
    reported = {d.get("name"): d for d in (worker or {}).get("datasets", []) if isinstance(d, dict)}
    items = []
    if data_dir.is_dir():
        for path in sorted(data_dir.iterdir()):
            if (
                not path.is_dir()
                or path.name.startswith(".")
                or path.name
                in (
                    "continuity",
                    "evaluations",
                    "runs",
                )
            ):
                continue
            manifest, error = _read(path / "manifest.json")
            entry: dict[str, Any] = {
                "name": path.name,
                "kind": "daily_shard" if _SHARD.match(path.name) else "dataset",
                "manifest": "present" if manifest is not None else error,
                "data_file_present": (path / "data.parquet").is_file(),
            }
            if manifest is not None:
                entry.update(
                    {
                        "manifest_sha256": hashlib.sha256(
                            (path / "manifest.json").read_bytes()
                        ).hexdigest(),
                        "data_sha256": manifest.get("data_sha256"),
                        "shard_day": manifest.get("shard_day"),
                        "first_frame_time": manifest.get("first_frame_time"),
                        "last_frame_time": manifest.get("last_frame_time"),
                        "row_count": manifest.get("row_count"),
                        "sample_interval_minutes": manifest.get("sample_interval_minutes"),
                        "generated_at": manifest.get("generated_at"),
                        "generation_mode": manifest.get("generation_mode"),
                        # Absent = produced before the field existed (contract v1:
                        # OI/funding availability unverified) — reported as such.
                        "temporal_contract_version": manifest.get("temporal_contract_version"),
                        "manifest_problems": _manifest_problems(path.name, manifest),
                    }
                )
            check = reported.get(path.name) or {}
            entry["verification"] = (
                ("verified" if check["verified_now"] else "failed")
                if "verified_now" in check
                else "not_checked"
            )
            if check.get("verification_error"):
                entry["verification_error"] = check["verification_error"]
            items.append(entry)
    return {"datasets": items, "overlaps": _overlaps(items)}


def _overlaps(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    spans = []
    for item in items:
        try:
            spans.append(
                (
                    datetime.fromisoformat(item["first_frame_time"]),
                    datetime.fromisoformat(item["last_frame_time"]),
                    item["name"],
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    found = []
    for i, (a0, a1, a) in enumerate(spans):
        for b0, b1, b in spans[i + 1 :]:
            if a0 <= b1 and b0 <= a1:
                found.append(
                    {"datasets": [a, b], "kind": "duplicate" if (a0, a1) == (b0, b1) else "overlap"}
                )
    return found


def shard_continuity(worker: dict[str, Any] | None) -> dict[str, Any]:
    """The worker's own day-by-day shard decisions (its authoritative
    continuity view), summarized: which completed days since its start have
    a preserved shard, and why the others do not."""
    if not worker or not isinstance(worker.get("days"), list):
        return {"available": False, "reason": "the research worker has not reported yet"}
    by_status: dict[str, list[str]] = {}
    for d in worker["days"]:
        by_status.setdefault(str(d.get("status")), []).append(str(d.get("day")))
    return {
        "available": True,
        "reported_at": worker.get("written_at"),
        "shards_start": worker.get("shards_start"),
        "days": len(worker["days"]),
        "by_status": {k: {"count": len(v), "days": v} for k, v in sorted(by_status.items())},
        "status_meanings": {
            "preserved": "the day's shard exists",
            "collection_gap": "no frames were collected that day; unrecoverable",
            "source_expired": "sources were pruned before a shard could be made; unrecoverable",
            "waiting_maturity": "labels not yet mature (the day plus the longest horizon)",
            "waiting_readiness": "waiting for sources to settle",
            "generate": "due for generation",
        },
    }


# -- contract summary, readiness, warnings -------------------------------------------------


def contract_summary(frames: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    shards: dict[str, list[str]] = {}
    for item in inventory["datasets"]:
        if item.get("manifest") != "present":
            continue
        version = item.get("temporal_contract_version")
        key = f"v{version}" if version is not None else "unrecorded"
        shards.setdefault(key, []).append(item["name"])
    return {
        "current_version": 2,
        "frames": frames.get("analyzed_by_contract") if frames.get("available") else None,
        "datasets": {k: {"count": len(v), "names": v} for k, v in sorted(shards.items())},
        "meaning": {
            "v2": "OI/funding inputs limited to rows stored by each frame's decision time — "
            "required by the registered Truffle evaluation",
            "v1": "analyzed before ingestion times were recorded: OI/funding availability "
            "unverified",
            "unrecorded": "a manifest without temporal_contract_version: produced before v2, "
            "availability unverified",
        },
    }


def readiness(worker: dict[str, Any] | None) -> dict[str, Any]:
    """Registration readiness exactly as the research worker computed it with
    the protocol's own functions; never recomputed here."""
    if not worker or "registrations" not in worker:
        return {
            "available": False,
            "reason": "the research worker has not reported registration readiness yet",
            "registrations": [],
        }
    return {
        "available": True,
        "reported_at": worker.get("written_at"),
        "registrations": worker["registrations"],
    }


def _warnings(
    frames: dict[str, Any],
    inventory: dict[str, Any],
    continuity: dict[str, Any],
    ready: dict[str, Any],
    worker_age: float | None,
) -> list[str]:
    out = []
    if worker_age is None:
        out.append("No research worker report was found.")
    elif worker_age > 2 * 3600:
        out.append(f"The research worker report is {worker_age / 3600:.1f}h old.")
    if frames.get("available") and frames["missing"]:
        out.append(
            f"{frames['missing']} of {frames['expected']} grid frames are missing since "
            f"{frames['observed_from'][:10]} (collection was not running)."
        )
    if frames.get("building"):
        out.append(f"{frames['building']} frame(s) were never finalized.")
    if frames.get("unanalyzed"):
        out.append(f"{frames['unanalyzed']} finalized frame(s) have no Sniffer analysis.")
    for item in inventory["datasets"]:
        if item.get("manifest") != "present":
            out.append(f"{item['name']}: manifest {item.get('manifest')}.")
        elif not item["data_file_present"]:
            out.append(f"{item['name']}: its data file is missing.")
        elif item.get("manifest_problems"):
            out.append(f"{item['name']}: " + "; ".join(item["manifest_problems"]))
    for o in inventory["overlaps"]:
        out.append(f"{o['kind']} intervals: {', '.join(o['datasets'])}.")
    for status in ("collection_gap", "source_expired"):
        days = (continuity.get("by_status") or {}).get(status)
        if days:
            out.append(f"{days['count']} day(s) without a shard ({status.replace('_', ' ')}).")
    for r in ready.get("registrations", []):
        if r.get("verification") != "verified":
            out.append(f"{r.get('registration_id')}: registration does not verify.")
        for p in r.get("partitions", []):
            if p.get("missing_past_days"):
                out.append(
                    f"{r.get('registration_id')}/{p.get('role')}: "
                    f"{len(p['missing_past_days'])} elapsed day(s) without a shard."
                )
    return out


def _worker_report(data_dir: Path) -> dict[str, Any] | None:
    report, _ = _read(data_dir / "continuity" / "status.json")
    return report


async def data_health(
    session: AsyncSession, data_dir: Path, *, now: datetime | None = None
) -> dict[str, Any]:
    """The full report, recomputed at most every CACHE_SECONDS (the response
    says when it was computed)."""
    cached = _cache.get(str(data_dir))
    if cached is not None and now is None and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    now = now or datetime.now(UTC)
    repo = SnifferRepository(session)
    frames = frame_coverage(await repo.frame_health_rows(), now, await repo.earliest_result_frame())
    worker = _worker_report(data_dir)
    inventory = shard_inventory(data_dir, worker)
    continuity = shard_continuity(worker)
    ready = readiness(worker)
    written = (worker or {}).get("written_at")
    try:
        age = (now - datetime.fromisoformat(written)).total_seconds() if written else None
    except ValueError:
        age = None
    report = {
        "computed_at": now.isoformat(),
        "cache_seconds": CACHE_SECONDS,
        "frames": frames,
        "shards": inventory,
        "shard_continuity": continuity,
        "temporal_contract": contract_summary(frames, inventory),
        "readiness": ready,
        "warnings": _warnings(frames, inventory, continuity, ready, age),
        "limitations": [
            "Data completeness describes collection, not scientific validity.",
            "Dataset verification is the worker's last explicit re-verification; a manifest that "
            "parses is not a verified dataset.",
            "Registration readiness is the research worker's last report (it runs every pass), "
            "not a live computation.",
            "Sniffer results older than their partition retention are reported as "
            "analysis_unknown, not missing.",
        ],
    }
    _cache[str(data_dir)] = (time.monotonic(), report)
    return report
