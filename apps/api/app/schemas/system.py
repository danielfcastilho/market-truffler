from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str


class ReadyResponse(BaseModel):
    status: str
    database: str


class SleepProtectionState(BaseModel):
    """macOS sleep protection as last reported by the host
    (`app.services.host_status`); `unknown` when no fresh report exists."""

    state: Literal["active", "inactive", "unknown"]
    reported_state: str | None
    observed_at: datetime | None
    checked_at: datetime
    stale_after_seconds: int
    detail: str
    power_source: str | None


class SystemInfo(BaseModel):
    environment: str
    version: str
    database: str
    authenticated_user: str | None
    uptime_seconds: float
    host_sleep_protection: SleepProtectionState | None = None
