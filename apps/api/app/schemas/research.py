from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ResearchStatus(BaseModel):
    """OINK CORP research data continuity, as last reported by the
    `research` worker (see `app.research.continuity`). `status` is
    "unavailable" before the worker has ever reported, "down" when its
    report is stale, "degraded" when a day is failing or a pending day's
    sources are about to expire, else "ok"."""

    status: Literal["ok", "degraded", "down", "unavailable"]
    reported_at: datetime | None = None
    generating_day: str | None = None
    last_preserved_day: str | None = None
    preserved_days: int | None = None
    waiting_days: int | None = None
    failing_days: int | None = None
    collection_gaps: int | None = None
    expiry_warnings: int | None = None
    unprotected_datasets: int | None = None
    backup_configured: bool = False
