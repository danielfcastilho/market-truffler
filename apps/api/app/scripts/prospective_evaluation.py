"""OINK CORP prospective evaluation (see `app.research.prospective`).

    python -m app.scripts.prospective_evaluation --campaign <campaign.json> \\
        --register --registration-id ID --hypotheses H1,H2 \\
        --validation 2026-10-26:2026-11-25 [--holdout 2026-11-26:2026-12-26] \\
        [--supersedes OLD_ID] [--dry-run]
    … --status [--registration-id ID]     eligibility and readiness per partition
    … --verify --registration-id ID       frozen artifacts, exposure log, runs
    … --exposure --registration-id ID     every recorded exposure event
    … --evaluate --registration-id ID --role validation
    … --evaluate --registration-id ID --role holdout --protected-holdout

Dates are UTC days; partition ends are exclusive. Nothing here runs on its
own: every evaluation is an explicit invocation. Reads datasets only through
the reservation-guarded loader.
"""

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from app.research.campaign import load_campaign
from app.research.experiment import ExperimentSpecError
from app.research.prospective import (
    Partition,
    ProspectiveError,
    attempts,
    build_registration,
    evaluate,
    load,
    load_all,
    register,
    registration_summary,
    verify_prospective_run,
    verify_registration,
)
from app.research.reservations import ReservedDataError
from app.research.runs import RUNS_DIRNAME, load_runs


def _partition(role: str, text: str) -> Partition:
    start, _, end = text.partition(":")
    return Partition(role=role, start=date.fromisoformat(start), end=date.fromisoformat(end))  # type: ignore[arg-type]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    for flag in ("--register", "--status", "--verify", "--exposure", "--evaluate"):
        mode.add_argument(flag, action="store_true")
    parser.add_argument("--campaign", required=True, type=Path)
    parser.add_argument("--registration-id")
    parser.add_argument("--hypotheses")
    parser.add_argument("--validation")
    parser.add_argument("--holdout")
    parser.add_argument("--supersedes")
    parser.add_argument("--role", choices=["validation", "holdout"])
    parser.add_argument("--protected-holdout", action="store_true")
    parser.add_argument("--acknowledge-prior-exposure", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    if args.data_dir is None:
        from app.core.config import get_settings

        args.data_dir = Path(get_settings().research_data_dir)
    now = datetime.now(UTC)
    try:
        campaign, specs = load_campaign(
            args.campaign, args.campaign.resolve().parent.parent / "experiments"
        )
        if args.register:
            if not (args.registration_id and args.hypotheses and args.validation):
                parser.error("--register needs --registration-id, --hypotheses, --validation")
            partitions = [_partition("validation", args.validation)]
            if args.holdout:
                partitions.append(_partition("holdout", args.holdout))
            registration = build_registration(
                campaign,
                specs,
                registration_id=args.registration_id,
                hypothesis_ids=args.hypotheses.split(","),
                partitions=partitions,
                registered_at=now,
                supersedes=args.supersedes,
            )
            print(json.dumps(registration.model_dump(mode="json"), indent=2))
            if not args.dry_run:
                print(f"registered: {register(args.data_dir, registration)}")
        elif args.status:
            for loaded in load_all(args.data_dir):
                if (
                    args.registration_id
                    and loaded.registration.registration_id != args.registration_id
                ):
                    continue
                print(json.dumps(registration_summary(loaded, args.data_dir, now), indent=2))
        elif args.exposure:
            loaded = load(args.data_dir, args.registration_id)
            for role in ("validation", "holdout"):
                for attempt in attempts(loaded.events, role):
                    print(f"{role} attempt {attempt['attempt_id']}: {attempt['outcome']}")
                    for event in attempt["events"]:
                        print(f"  #{event['seq']} {event['at']} {event['type']}")
            if not loaded.events:
                print("no exposure recorded")
        elif args.verify:
            loaded = load(args.data_dir, args.registration_id)
            problems = verify_registration(loaded, campaign, specs)
            for record in load_runs(args.data_dir / RUNS_DIRNAME).records:
                if record.get("registration_id") == args.registration_id:
                    problems += verify_prospective_run(args.data_dir, record)
            print("VERIFIED" if not problems else "INVALID")
            for problem in problems:
                print(f"  - {problem}")
            sys.exit(1 if problems else 0)
        else:
            if not (args.registration_id and args.role):
                parser.error("--evaluate needs --registration-id and --role")
            event = evaluate(
                args.data_dir,
                campaign,
                specs,
                args.registration_id,
                args.role,
                now=now,
                protected_holdout=args.protected_holdout,
                acknowledge_prior_exposure=args.acknowledge_prior_exposure,
            )
            print(json.dumps(event, indent=2))
    except (ProspectiveError, ExperimentSpecError, ReservedDataError, OSError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
