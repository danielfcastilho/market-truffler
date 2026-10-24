"""OINK CORP knowledge ledger (see `app.research.knowledge`).

    python -m app.scripts.knowledge_ledger --derive \\
        --campaign research/oink_corp/campaigns/<campaign_id>.json \\
        [--data-dir research/oink_corp/data] [--dry-run]
    python -m app.scripts.knowledge_ledger --verify --campaign …

`--derive` re-derives the campaign's knowledge from its immutable runs and
appends a revision under `research/oink_corp/knowledge/<campaign_id>/` only
if it differs from the latest one (after verifying it against the runs).
`--verify` checks every revision of the chain against the evidence it
cites, and reports runs the latest revision does not cite yet. Reads run
records only — never datasets, never reruns anything.
"""

import argparse
import sys
from pathlib import Path

from app.research.campaign import load_campaign
from app.research.experiment import ExperimentSpecError
from app.research.knowledge import (
    LedgerError,
    content_hash,
    derive_ledger,
    load_revisions,
    missing_evidence,
    verify_revision,
    write_revision,
)
from app.research.runs import RUNS_DIRNAME, load_runs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--derive", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--campaign", required=True, type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--knowledge-dir", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="--derive: print, write nothing")
    args = parser.parse_args()

    research_root = args.campaign.resolve().parent.parent
    knowledge_root = args.knowledge_dir or research_root / "knowledge"
    if args.data_dir is None:
        from app.core.config import get_settings

        args.data_dir = Path(get_settings().research_data_dir)
    try:
        campaign, specs = load_campaign(args.campaign, research_root / "experiments")
    except (ExperimentSpecError, OSError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        sys.exit(1)
    records = load_runs(args.data_dir / RUNS_DIRNAME).records

    if args.derive:
        try:
            derived = derive_ledger(campaign, specs, records)
        except LedgerError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            sys.exit(1)
        revisions = load_revisions(knowledge_root, campaign.campaign_id)
        candidate = {
            **derived,
            "revision": len(revisions) + 1,
            "previous_revision_hash": revisions[-1]["content_hash"] if revisions else None,
        }

        candidate["content_hash"] = content_hash(candidate)
        problems = verify_revision(
            candidate, campaign, specs, records, revisions[-1] if revisions else None
        )
        if problems:  # derivation and verification disagree: a bug, never written
            print("refused: derived ledger fails verification:", *problems, sep="\n  ")
            sys.exit(1)
        for h in derived["hypotheses"]:
            print(
                f"{h['hypothesis_id']} ({h['direction']}): {h['knowledge_status']}, "
                f"{h['validation_status']}; {sum(e['current'] for e in h['evidence'])} current "
                f"of {len(h['evidence'])} runs"
            )
        if args.dry_run:
            return
        path, revision = write_revision(knowledge_root, derived)
        if path is None:
            print(f"unchanged: latest revision {revision['revision']} already states this")
        else:
            print(f"written: {path} (revision {revision['revision']})")
        return

    revisions = load_revisions(knowledge_root, campaign.campaign_id)
    if not revisions:
        print(f"no ledger for {campaign.campaign_id}")
        sys.exit(1)
    failed = False
    for i, revision in enumerate(revisions):
        problems = verify_revision(
            revision, campaign, specs, records, revisions[i - 1] if i else None
        )
        failed |= bool(problems)
        print(f"revision {revision.get('revision')}: {'VERIFIED' if not problems else 'INVALID'}")
        for problem in problems:
            print(f"  - {problem}")
    stale = missing_evidence(revisions[-1], campaign, records)
    if stale:
        print("latest revision does not cite these newer runs (run --derive):")
        for item in stale:
            print(f"  - {item}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
