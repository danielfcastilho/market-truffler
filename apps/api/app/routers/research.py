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
from app.schemas.research import ResearchEvent, ResearchStatus

router = APIRouter(tags=["research"])

#: The worker writes a heartbeat every minute, whatever it is doing.
_HEARTBEAT_STALE = timedelta(minutes=5)
#: Reports from before heartbeats existed: a pass may legitimately be busy
#: generating one shard for hours (the worker's per-shard limit is 6h).
_MAX_GENERATION = timedelta(hours=6, minutes=30)


def _parse(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _event(value: Any) -> ResearchEvent | None:
    if not isinstance(value, dict):
        return None
    return ResearchEvent(
        day=value.get("day"),
        at=_parse(value.get("at")),
        kind=value.get("kind"),
        error=value.get("error") or value.get("reason"),
    )


def _backup_status(
    backup: Any, unprotected: int
) -> Literal["protected", "unprotected", "failing", "not_configured"]:
    if not isinstance(backup, dict):
        return "not_configured"
    if backup.get("error") or backup.get("errors"):
        return "failing"
    if backup.get("checked_at") and unprotected == 0:
        return "protected"
    return "unprotected"


def _health(
    report: dict[str, Any], fresh: bool, generating: str | None
) -> tuple[Literal["healthy", "recovering", "degraded", "blocked", "unknown"], str]:
    summary = report.get("summary") or {}
    worker = report.get("worker") or {}
    if not fresh:
        return "unknown", "worker not reporting"
    blocked = summary.get("blocked_days") or 0
    failing = summary.get("failing_days") or 0
    expiring = summary.get("expiry_warnings") or 0
    if blocked:
        return "blocked", f"{blocked} day(s) failed repeatedly — needs attention"
    if failing and expiring:
        return "blocked", "a failing day's sources expire within days"
    if failing:
        failure = report.get("last_failure") or {}
        kind = f" ({failure['kind']})" if failure.get("kind") else ""
        return "degraded", f"{failing} day(s) failing{kind}, retrying"
    if expiring:
        return "degraded", f"{expiring} unpreserved day(s) near source expiry"
    pass_error = _parse((worker.get("last_pass_error") or {}).get("at"))
    finished = _parse(worker.get("last_pass_finished_at"))
    if pass_error is not None and (finished is None or pass_error > finished):
        return "degraded", "the last pass crashed"
    if generating:
        return "recovering", f"generating {generating}"
    due = summary.get("due_days") or 0
    if due:
        return "recovering", f"{due} day(s) due for generation"
    return "healthy", "all recoverable days preserved or waiting normally"


def research_status_from_file(path: Path, now: datetime, pass_interval: float) -> ResearchStatus:
    try:
        report = json.loads(path.read_text())
    except (OSError, ValueError):
        return ResearchStatus(
            status="unknown", reason="no report from the worker yet", backup_status="unknown"
        )

    worker = report.get("worker") or {}
    summary = report.get("summary") or {}
    generating = worker.get("generating") or {}
    reported_at = _parse(report.get("written_at"))
    heartbeat = _parse(worker.get("heartbeat_at"))
    started = _parse(generating.get("started_at"))

    if heartbeat is not None:
        # a restarted worker writes a fresh field without `stopped_at`
        fresh = now - heartbeat <= _HEARTBEAT_STALE and not worker.get("stopped_at")
    else:
        fresh_window = timedelta(seconds=2 * pass_interval + 600)
        fresh = (reported_at is not None and now - reported_at <= fresh_window) or (
            started is not None and now - started <= _MAX_GENERATION
        )
    status, reason = _health(report, fresh, generating.get("generating"))
    unprotected = summary.get("unprotected_datasets") or 0
    backup = report.get("backup")

    return ResearchStatus(
        status=status,
        reason=reason,
        reported_at=reported_at,
        heartbeat_at=heartbeat,
        generating_day=generating.get("generating"),
        generating_since=started,
        last_preserved_day=summary.get("last_preserved_day"),
        preserved_days=summary.get("preserved_days"),
        missing_days=summary.get("missing_days") or [],
        waiting_days=summary.get("waiting_days"),
        due_days=summary.get("due_days"),
        failing_days=summary.get("failing_days"),
        blocked_days=summary.get("blocked_days"),
        collection_gaps=summary.get("collection_gaps"),
        unrecoverable_days=summary.get("unrecoverable_days"),
        expiry_warnings=summary.get("expiry_warnings"),
        last_success=_event(report.get("last_success")),
        last_failure=_event(report.get("last_failure")),
        last_interruption=_event(report.get("last_interruption")),
        datasets=summary.get("datasets"),
        unprotected_datasets=unprotected,
        backup_configured=isinstance(backup, dict),
        backup_status=_backup_status(backup, unprotected),
        backup_checked_at=_parse((backup or {}).get("checked_at")),
        backup_error=(backup or {}).get("error"),
    )


@router.get("/api/research/status", response_model=ResearchStatus)
async def research_status(current_user: CurrentUserDep) -> ResearchStatus:
    settings = get_settings()
    path = Path(settings.research_data_dir) / "continuity" / "status.json"
    return research_status_from_file(
        path, datetime.now(UTC), settings.research_pass_interval_seconds
    )
