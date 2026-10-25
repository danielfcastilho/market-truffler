"""Prospective registration readiness, for the research worker's report.

Each registration is verified and its partitions' states taken with the
protocol's own functions — `prospective.verify_registration` /
`prospective.partition_state` for campaign-claim registrations,
`truffle_evaluation.verify_truffle_registration` /
`prospective.registration_partition_state` for Truffle registrations — so
this can never disagree with what an evaluation would accept. It only adds
names for where each partition is in time and which elapsed days lack a
shard. Read-only: it writes no event, opens no dataset and reads no label.

Phases (per partition):
- awaiting_window — the partition has not started;
- accumulating — inside the partition; shards accrue day by day;
- awaiting_eligibility — the partition ended but the protocol's
  eligibility time (all shards, or the readiness fallback) has not come;
- eligible — the protocol would accept an evaluation now (subject to
  verification; the holdout additionally needs --protected-holdout);
- evaluated / failed / exposure_uncertain / authorized_only — an attempt
  exists (its outcome, from the exposure log).
"""

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.research import prospective, truffle_evaluation
from app.research.campaign import load_campaign
from app.research.continuity import shard_name
from app.research.reservations import day_start, read_registration_file, registration_dirs


def _phase(state: str, start: date, end: date, now: datetime) -> str:
    if state == "completed":
        return "evaluated"
    if state not in ("awaiting_data", "eligible"):
        return state
    if now < day_start(start):
        return "awaiting_window"
    if now < day_start(end):
        return "accumulating"
    return "awaiting_eligibility" if state == "awaiting_data" else "eligible"


def _partition(
    data_dir: Path, role: str, state: dict[str, Any], now: datetime, holdout: bool
) -> dict[str, Any]:
    start, end = date.fromisoformat(state["start"]), date.fromisoformat(state["end"])
    today = now.date()
    elapsed = [start + timedelta(days=i) for i in range((min(end, today) - start).days)]
    missing = [
        d.isoformat() for d in elapsed if not (data_dir / shard_name(d) / "manifest.json").exists()
    ]
    phase = _phase(state["state"], start, end, now)
    blocking = []
    if phase in ("awaiting_window", "accumulating", "awaiting_eligibility"):
        blocking.append(f"not eligible until {state['eligible_from']}")
    if holdout and phase == "eligible":
        blocking.append("the protected holdout needs an explicit --protected-holdout")
    return {
        **state,
        "phase": phase,
        "days_elapsed": len(elapsed),
        "missing_past_days": missing,
        "blocking": blocking,
    }


def registration_readiness(
    data_dir: Path,
    *,
    campaigns_dir: Path,
    experiments_dir: Path,
    ranking_dir: Path,
    now: datetime,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for directory in registration_dirs(data_dir):
        try:
            kind = read_registration_file(directory)["registration"].get("kind")
            if kind is None:
                loaded = prospective.load(data_dir, directory.name)
                campaign_path = campaigns_dir / f"{loaded.registration.campaign_id}.json"
                campaign, specs = load_campaign(campaign_path, experiments_dir)
                problems = prospective.verify_registration(loaded, campaign, specs)
                registration: Any = loaded.registration
                states = [
                    prospective.partition_state(loaded, p.role, data_dir, now)
                    for p in registration.partitions
                ]
                stored_hash = loaded.stored_hash
            else:
                truffle = truffle_evaluation.load_truffle(data_dir, directory.name)
                problems = truffle_evaluation.verify_truffle_registration(
                    truffle, ranking_dir / "candidates", ranking_dir / "qualifications"
                )
                registration = truffle.registration
                states = [
                    prospective.registration_partition_state(
                        registration, truffle.events, p.role, data_dir, now
                    )
                    for p in registration.partitions
                ]
                stored_hash = truffle.stored_hash
        except (OSError, ValueError) as exc:
            out.append(
                {
                    "registration_id": directory.name,
                    "verification": "error",
                    "problems": [str(exc)],
                    "partitions": [],
                }
            )
            continue
        partitions = [
            _partition(data_dir, s["role"], s, now, holdout=s["role"] == "holdout") for s in states
        ]
        for p in partitions:
            if problems:
                p["blocking"].insert(0, "the registration does not verify")
            p["eligible_now"] = p["phase"] == "eligible" and not p["blocking"]
        out.append(
            {
                "registration_id": registration.registration_id,
                "kind": kind or "campaign_claims",
                "registration_hash": stored_hash,
                "registered_at": registration.registered_at.isoformat(),
                "verification": "verified" if not problems else "invalid",
                "problems": problems,
                "partitions": partitions,
            }
        )
    return out
