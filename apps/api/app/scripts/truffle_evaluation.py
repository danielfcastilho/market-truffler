"""OINK CORP prospective Truffle evaluation (see `app.research.truffle_evaluation`).

    python -m app.scripts.truffle_evaluation --register --registration-id ID \\
        --validation 2026-12-27:2027-01-26 [--holdout 2027-01-27:2027-02-26] [--dry-run]
    … --status [--registration-id ID]     lifecycle per partition
    … --verify --registration-id ID       frozen hashes against the current specs
    … --evaluate --registration-id ID --role validation
    … --evaluate --registration-id ID --role holdout --protected-holdout

Registers the configured live pairs (ranking candidate, qualification spec)
of both directions. Dates are UTC days; partition ends are exclusive.
Nothing runs on its own: every evaluation is an explicit invocation, refused
before the partition is eligible. Reads datasets only through the
reservation-guarded loader.
"""

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from app.core.config import get_settings
from app.research.experiment import ExperimentSpecError
from app.research.prospective import Partition, ProspectiveError
from app.research.reservations import ReservedDataError
from app.research.truffle_evaluation import (
    build_truffle_registration,
    evaluate_truffles,
    load_all_truffles,
    load_truffle,
    register_truffles,
    truffle_status,
    verify_truffle_registration,
)


def _partition(role: str, text: str) -> Partition:
    start, _, end = text.partition(":")
    return Partition.model_validate(
        {"role": role, "start": date.fromisoformat(start), "end": date.fromisoformat(end)}
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    for flag in ("--register", "--status", "--verify", "--evaluate"):
        mode.add_argument(flag, action="store_true")
    parser.add_argument("--registration-id")
    parser.add_argument("--validation")
    parser.add_argument("--holdout")
    parser.add_argument("--role", choices=["validation", "holdout"])
    parser.add_argument("--protected-holdout", action="store_true")
    parser.add_argument("--acknowledge-prior-exposure", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--ranking-dir", type=Path)
    args = parser.parse_args()
    settings = get_settings()
    data_dir = args.data_dir or Path(settings.research_data_dir)
    ranking_dir = args.ranking_dir or Path(settings.research_ranking_dir)
    candidates, qualifications = ranking_dir / "candidates", ranking_dir / "qualifications"
    now = datetime.now(UTC)
    try:
        if args.register:
            if not (args.registration_id and args.validation):
                parser.error("--register needs --registration-id and --validation")
            partitions = [_partition("validation", args.validation)]
            if args.holdout:
                partitions.append(_partition("holdout", args.holdout))
            registration = build_truffle_registration(
                candidates,
                qualifications,
                registration_id=args.registration_id,
                partitions=partitions,
                registered_at=now,
                pairs=[
                    (
                        settings.live_ranking_long_candidate,
                        settings.live_truffle_long_qualification,
                    ),
                    (
                        settings.live_ranking_short_candidate,
                        settings.live_truffle_short_qualification,
                    ),
                ],
            )
            print(json.dumps(registration.model_dump(mode="json"), indent=2))
            if not args.dry_run:
                print(f"registered: {register_truffles(data_dir, registration)}")
        elif args.status:
            for loaded in load_all_truffles(data_dir):
                if (
                    args.registration_id
                    and loaded.registration.registration_id != args.registration_id
                ):
                    continue
                print(json.dumps(truffle_status(loaded, data_dir, now), indent=2))
        elif args.verify:
            problems = verify_truffle_registration(
                load_truffle(data_dir, args.registration_id), candidates, qualifications
            )
            print("VERIFIED" if not problems else "INVALID")
            for problem in problems:
                print(f"  - {problem}")
            sys.exit(1 if problems else 0)
        else:
            if not (args.registration_id and args.role):
                parser.error("--evaluate needs --registration-id and --role")
            event = evaluate_truffles(
                data_dir,
                candidates,
                qualifications,
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
