"""Prospective research continuity alerts, derived by the research worker
each pass from facts it already owns — never a second reading of the
protocol:

- its per-day shard decisions (`continuity.decide_day`: a day is only
  judged once it is complete and its labels have matured, so a day in
  progress, an immature day or a future window never alerts);
- registration readiness (`app.research.readiness`, the protocol's own
  verification and partition states);
- scheduled integrity verification (`app.research.integrity`).

Conditions, aggregated per registration partition (one alert per problem,
listing its days — never one per day):

- `registration_invalid` (critical): the registration does not verify;
- `shard_integrity_failed` (critical): a shard of a partition day failed
  verification;
- `shard_unrecoverable` (warning): a completed partition day has no shard
  and cannot get one (collection gap, sources expired);
- `shard_generation_failing` (warning): a partition day's shard keeps
  failing to generate;
- `verification_overdue` (warning): a partition day's shard was not
  re-verified within twice the interval;
- `awaiting_data` (info): the partition is ahead (awaiting its window or
  accumulating).

Worker conditions (registration `research_worker`): `generation_lock_lost`
(warning) — the last pass lost its generation lock (its database session
ended) and stopped before publishing.

Lifecycle (`continuity/alerts.json`, operational state): an alert's id is
`<registration>/<role>/<condition>`; while its condition holds it stays
active with its first-observed time; when it no longer holds it is
resolved; if it returns it is active again with a new first-observed time
and an incremented occurrence count. Alerts are only updated by a
completed pass, so a restart never resolves anything by itself.
"""

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.research.continuity import shard_name

ALERTS_FILE = "alerts.json"
MAX_RESOLVED = 50
_UNRECOVERABLE = ("collection_gap", "source_expired")


def _alert(
    registration: str,
    role: str | None,
    condition: str,
    severity: str,
    explanation: str,
    days: list[str] | None = None,
) -> dict[str, Any]:
    days = sorted(days or [])
    return {
        "id": f"{registration}/{role or '-'}/{condition}",
        "registration_id": registration,
        "role": role,
        "condition": condition,
        "severity": severity,
        "explanation": explanation,
        "days": days,
        "interval": [days[0], days[-1]] if days else None,
    }


def derive_alerts(
    registrations: list[dict[str, Any]],
    days: list[dict[str, Any]],
    verification: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """The conditions that hold now (no lifecycle), deterministic order."""
    by_day = {str(d.get("day")): d for d in days}
    out = []
    for r in registrations:
        rid = str(r.get("registration_id"))
        if r.get("verification") != "verified":
            out.append(
                _alert(
                    rid,
                    None,
                    "registration_invalid",
                    "critical",
                    "the registration does not verify: " + "; ".join(r.get("problems") or []),
                )
            )
        for p in r.get("partitions", []):
            role = str(p.get("role"))
            start, end = date.fromisoformat(p["start"]), date.fromisoformat(p["end"])
            window = [d for d, v in by_day.items() if start <= date.fromisoformat(d) < end]
            lost = [d for d in window if by_day[d].get("status") in _UNRECOVERABLE]
            failing = [
                d
                for d in window
                if by_day[d].get("attempts")
                and by_day[d].get("status") not in ("preserved", *_UNRECOVERABLE)
            ]
            shards = {d: verification.get(shard_name(date.fromisoformat(d))) for d in window}
            corrupt = [d for d, v in shards.items() if v and v.get("status") == "failed"]
            overdue = [d for d, v in shards.items() if v and v.get("overdue")]
            if corrupt:
                out.append(
                    _alert(
                        rid,
                        role,
                        "shard_integrity_failed",
                        "critical",
                        f"{len(corrupt)} shard(s) of this partition failed integrity verification",
                        corrupt,
                    )
                )
            if lost:
                out.append(
                    _alert(
                        rid,
                        role,
                        "shard_unrecoverable",
                        "warning",
                        f"{len(lost)} completed day(s) of this partition have no shard and cannot "
                        "get one; the evaluation will record them as missing",
                        lost,
                    )
                )
            if failing:
                out.append(
                    _alert(
                        rid,
                        role,
                        "shard_generation_failing",
                        "warning",
                        f"{len(failing)} day(s) of this partition keep failing to generate",
                        failing,
                    )
                )
            if overdue:
                out.append(
                    _alert(
                        rid,
                        role,
                        "verification_overdue",
                        "warning",
                        f"{len(overdue)} shard(s) were not re-verified within twice the interval",
                        overdue,
                    )
                )
            if p.get("phase") in ("awaiting_window", "accumulating"):
                out.append(
                    _alert(
                        rid,
                        role,
                        "awaiting_data",
                        "info",
                        f"{p.get('phase', '').replace('_', ' ')}; eligible from "
                        f"{str(p.get('eligible_from'))[:10]}",
                    )
                )
    return out


#: A backup older than this (no fully successful run since) is overdue.
BACKUP_OVERDUE = timedelta(hours=36)


def derive_backup_alerts(
    backup: dict[str, Any] | None, now: datetime, recovery_interval: timedelta
) -> list[dict[str, Any]]:
    """Backup and recovery-test conditions (`app.research.backup`), as
    global alerts (registration `research_backup`). `backup` is the worker's
    backup summary, None when no destination is configured."""
    rid = "research_backup"
    if backup is None:
        return [
            _alert(
                rid,
                None,
                "backup_not_configured",
                "warning",
                "no independent backup destination is configured: research datasets, "
                "registrations and exposure logs exist on this host only",
            )
        ]
    out = []
    last_success = backup.get("last_success_at")
    if backup.get("error"):
        out.append(
            _alert(
                rid,
                None,
                "backup_destination_unavailable",
                "warning" if last_success else "critical",
                "the backup destination cannot be used"
                + ("" if last_success else " and no verified backup exists")
                + f": {backup['error']}",
            )
        )
    if backup.get("integrity_errors"):
        out.append(
            _alert(
                rid,
                None,
                "backup_integrity_failed",
                "critical",
                f"{len(backup['integrity_errors'])} item(s) do not match their checksums "
                "(source or copy): " + ", ".join(sorted(backup["integrity_errors"])[:5]),
            )
        )
    other = sorted(set(backup.get("errors") or {}) - set(backup.get("integrity_errors") or []))
    if other:
        out.append(
            _alert(
                rid,
                None,
                "backup_failed",
                "warning",
                f"{len(other)} item(s) could not be backed up",
            )
        )
    if not backup.get("error") and backup.get("unprotected"):
        out.append(
            _alert(
                rid,
                None,
                "backup_incomplete",
                "warning",
                f"{len(backup['unprotected'])} research item(s) have no verified copy yet",
            )
        )
    if backup.get("checked_at") and (
        last_success is None or now - datetime.fromisoformat(last_success) > BACKUP_OVERDUE
    ):
        out.append(
            _alert(
                rid, None, "backup_overdue", "warning", "no fully successful backup within 36 hours"
            )
        )
    test = backup.get("recovery_test")
    if test is None:
        out.append(
            _alert(rid, None, "recovery_untested", "warning", "no recovery test has run yet")
        )
    elif test.get("status") == "failed":
        out.append(
            _alert(
                rid,
                None,
                "recovery_test_failed",
                "critical",
                f"{len(test.get('failures') or {})} restored item(s) were missing or corrupt",
            )
        )
    elif test.get("status") != "passed":
        out.append(
            _alert(
                rid,
                None,
                "recovery_test_unavailable",
                "warning",
                f"the last recovery test could not run: {test.get('error')}",
            )
        )
    elif now - datetime.fromisoformat(test["tested_at"]) > 3 * recovery_interval:
        out.append(
            _alert(
                rid,
                None,
                "recovery_test_overdue",
                "warning",
                "the last passing recovery test is old",
            )
        )
    return out


def derive_worker_alerts(worker: dict[str, Any]) -> list[dict[str, Any]]:
    """Conditions of the worker's last completed pass."""
    if (worker.get("last_pass_summary") or {}).get("lock_lost"):
        return [
            _alert(
                "research_worker",
                None,
                "generation_lock_lost",
                "warning",
                "the last pass lost its generation lock (its database session ended): "
                "generation stopped before publishing and is retried next pass",
            )
        ]
    return []


def _load(continuity_dir: Path) -> dict[str, Any]:
    try:
        value = json.loads((continuity_dir / ALERTS_FILE).read_text())
        if isinstance(value, dict) and isinstance(value.get("active"), dict):
            return value
    except (OSError, ValueError):
        pass
    return {"active": {}, "resolved": [], "occurrences": {}}


def update_alerts(
    continuity_dir: Path, current: list[dict[str, Any]], now: datetime
) -> dict[str, Any]:
    """Apply one pass's conditions to the stored lifecycle and save it.
    Returns the published view: active (sorted by severity, then id) and
    recently resolved."""
    stored = _load(continuity_dir)
    active: dict[str, dict[str, Any]] = stored["active"]
    occurrences: dict[str, int] = stored.get("occurrences", {})
    resolved: list[dict[str, Any]] = stored.get("resolved", [])
    seen = set()
    for alert in current:
        seen.add(alert["id"])
        if alert["id"] in active:
            active[alert["id"]] = {
                **alert,
                "first_observed": active[alert["id"]]["first_observed"],
                "occurrence": active[alert["id"]].get("occurrence", 1),
                "last_checked": now.isoformat(),
                "state": "active",
            }
        else:
            occurrences[alert["id"]] = occurrences.get(alert["id"], 0) + 1
            active[alert["id"]] = {
                **alert,
                "first_observed": now.isoformat(),
                "occurrence": occurrences[alert["id"]],
                "last_checked": now.isoformat(),
                "state": "active",
            }
    for alert_id in [a for a in active if a not in seen]:
        resolved.append(
            {**active.pop(alert_id), "state": "resolved", "resolved_at": now.isoformat()}
        )
    resolved = resolved[-MAX_RESOLVED:]
    continuity_dir.mkdir(parents=True, exist_ok=True)
    path = continuity_dir / ALERTS_FILE
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(
        json.dumps(
            {"active": active, "resolved": resolved, "occurrences": occurrences},
            indent=2,
            sort_keys=True,
        )
    )
    tmp.replace(path)
    order = {"critical": 0, "warning": 1, "info": 2}
    return {
        "checked_at": now.isoformat(),
        "active": sorted(active.values(), key=lambda a: (order.get(a["severity"], 3), a["id"])),
        "recently_resolved": list(reversed(resolved[-10:])),
    }
