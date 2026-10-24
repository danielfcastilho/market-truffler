from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ResearchEvent(BaseModel):
    day: str | None = None
    at: datetime | None = None
    kind: str | None = None
    error: str | None = None


class ResearchStatus(BaseModel):
    """OINK CORP research data continuity, as last reported by the
    `research` worker (see `app.research.continuity`). `status`:

    - "unknown": no report, or the worker's heartbeat is stale;
    - "blocked": a day keeps failing (needs a human), or a recoverable
      day's sources are about to expire while it is failing;
    - "degraded": a day has an unresolved failure, a recoverable day's
      sources are about to expire, the last pass crashed, or a configured
      backup is failing;
    - "recovering": recoverable days are due or being generated;
    - "healthy": none of the above.

    Backup protection is reported separately (`backup_status`) — it never
    makes continuity look healthy or not, and is "not_configured" unless a
    destination exists and its copies were verified."""

    status: Literal["healthy", "recovering", "degraded", "blocked", "unknown"]
    reason: str
    reported_at: datetime | None = None
    heartbeat_at: datetime | None = None
    generating_day: str | None = None
    generating_since: datetime | None = None
    last_preserved_day: str | None = None
    preserved_days: int | None = None
    missing_days: list[str] = []
    waiting_days: int | None = None
    due_days: int | None = None
    failing_days: int | None = None
    blocked_days: int | None = None
    collection_gaps: int | None = None
    unrecoverable_days: int | None = None
    expiry_warnings: int | None = None
    last_success: ResearchEvent | None = None
    last_failure: ResearchEvent | None = None
    last_interruption: ResearchEvent | None = None
    datasets: int | None = None
    unprotected_datasets: int | None = None
    backup_configured: bool = False
    backup_status: Literal["protected", "unprotected", "failing", "not_configured", "unknown"]
    backup_checked_at: datetime | None = None
    backup_error: str | None = None
