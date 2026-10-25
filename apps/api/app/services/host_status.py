"""macOS host sleep protection, as reported by the host itself.

The api runs in a Linux container and cannot see macOS processes or power
assertions. `infra/keep-awake.sh` (a per-user launchd agent on the host)
checks every minute that macOS has registered its `caffeinate` assertions
and atomically writes the result to `var/host-status/keep-awake.json`,
bind-mounted read-only at `settings.host_status_dir`. This module only
reads that one fixed file name — no path comes from a request.

What it can say: whether, as of `observed_at`, the host's managed agent held
a registered sleep-prevention assertion. A report older than `STALE_AFTER`
proves nothing about now and is `unknown`, as is a missing or unreadable
one. It cannot say whether the host slept in the past, nor whether
collection is healthy (Vitals' frame rows say that).
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

FILE_NAME = "keep-awake.json"
#: The agent reports every 60 s: three missed reports make it stale.
STALE_AFTER = timedelta(seconds=180)
#: Clock skew tolerated for a report stamped slightly in the future.
_FUTURE_TOLERANCE = timedelta(seconds=60)
_MAX_BYTES = 4096
_REPORTED_STATES = {"active", "inactive", "stopped"}


@dataclass(frozen=True)
class SleepProtection:
    state: Literal["active", "inactive", "unknown"]
    reported_state: str | None
    observed_at: datetime | None
    checked_at: datetime
    stale_after_seconds: int
    detail: str
    power_source: str | None


def read_sleep_protection(directory: Path, now: datetime | None = None) -> SleepProtection:
    now = now or datetime.now(UTC)

    def unknown(
        detail: str, reported: str | None = None, observed: datetime | None = None
    ) -> SleepProtection:
        return SleepProtection(
            "unknown", reported, observed, now, int(STALE_AFTER.total_seconds()), detail, None
        )

    try:
        with (directory / FILE_NAME).open("rb") as f:
            raw = f.read(_MAX_BYTES + 1)
    except FileNotFoundError:
        return unknown("no host report (infra/keep-awake.sh has never run on this host)")
    except OSError:
        return unknown("host report unreadable")
    if len(raw) > _MAX_BYTES:
        return unknown("host report malformed")
    try:
        report = json.loads(raw)
        reported = str(report["state"])
        observed = datetime.fromisoformat(str(report["observed_at"]))
        if report.get("schema") != 1 or reported not in _REPORTED_STATES:
            raise ValueError
        if observed.tzinfo is None:
            raise ValueError
    except (ValueError, KeyError, TypeError):
        return unknown("host report malformed")
    if observed > now + _FUTURE_TOLERANCE:
        return unknown("host report timestamp is in the future", reported, observed)
    if now - observed > STALE_AFTER:
        return unknown(f"no host report since {observed.isoformat()}", reported, observed)
    power = report.get("power_source")
    return SleepProtection(
        "active" if reported == "active" else "inactive",
        reported,
        observed,
        now,
        int(STALE_AFTER.total_seconds()),
        str(report.get("detail") or "")[:200],
        str(power)[:40] if power else None,
    )
