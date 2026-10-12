"""Read-only OINK CORP research-data status for Vitals.

Reads the status file the `research` worker writes after every pass
(`<research_data_dir>/continuity/status.json`) as plain JSON — it never
imports research code (see `tests/test_architecture_boundaries.py`) and
never triggers any research work.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter

from app.core.config import get_settings
from app.core.deps import CurrentUserDep
from app.schemas.research import ResearchStatus

router = APIRouter(tags=["research"])

#: A pass may legitimately be busy generating one shard for hours (the
#: worker's per-shard limit is 6h); beyond that, no fresh report means the
#: worker isn't running.
_MAX_GENERATION = timedelta(hours=6, minutes=30)


def _parse(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def research_status_from_file(path: Path, now: datetime, pass_interval: float) -> ResearchStatus:
    try:
        report = json.loads(path.read_text())
    except (OSError, ValueError):
        return ResearchStatus(status="unavailable")

    worker = report.get("worker") or {}
    summary = report.get("summary") or {}
    generating = worker.get("generating") or {}
    reported_at = _parse(report.get("written_at"))
    started = _parse(generating.get("started_at"))

    fresh_window = timedelta(seconds=2 * pass_interval + 600)
    fresh = (reported_at is not None and now - reported_at <= fresh_window) or (
        started is not None and now - started <= _MAX_GENERATION
    )
    status: Literal["ok", "degraded", "down"]
    if not fresh:
        status = "down"
    elif summary.get("failing_days") or summary.get("expiry_warnings"):
        status = "degraded"
    else:
        status = "ok"

    return ResearchStatus(
        status=status,
        reported_at=reported_at,
        generating_day=generating.get("generating"),
        last_preserved_day=summary.get("last_preserved_day"),
        preserved_days=summary.get("preserved_days"),
        waiting_days=summary.get("waiting_days"),
        failing_days=summary.get("failing_days"),
        collection_gaps=summary.get("collection_gaps"),
        expiry_warnings=summary.get("expiry_warnings"),
        unprotected_datasets=summary.get("unprotected_datasets"),
        backup_configured=bool(summary.get("backup_configured", False)),
    )


@router.get("/api/research/status", response_model=ResearchStatus)
async def research_status(current_user: CurrentUserDep) -> ResearchStatus:
    settings = get_settings()
    path = Path(settings.research_data_dir) / "continuity" / "status.json"
    return research_status_from_file(
        path, datetime.now(UTC), settings.research_pass_interval_seconds
    )
