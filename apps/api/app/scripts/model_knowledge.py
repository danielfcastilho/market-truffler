"""OINK CORP model knowledge (see `app.research.model_knowledge`).

    python -m app.scripts.model_knowledge --sync [--dry-run]
    python -m app.scripts.model_knowledge --verify

`--sync` derives the knowledge of the configured live ranking models and
qualification policies from their spec files and the artifacts citing their
hashes, and appends a revision only where it changed (idempotent).
`--verify` re-checks every revision of those ledgers.
"""

import argparse
import json
import sys
from pathlib import Path

from app.core.config import get_settings
from app.research.knowledge import LedgerError
from app.research.model_knowledge import derive_all, identities, sync, verify_ledger


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sync", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--ranking-dir", type=Path)
    parser.add_argument("--knowledge-dir", type=Path)
    args = parser.parse_args()
    settings = get_settings()
    data_dir = args.data_dir or Path(settings.research_data_dir)
    ranking_dir = args.ranking_dir or Path(settings.research_ranking_dir)
    knowledge_dir = args.knowledge_dir or Path(settings.research_knowledge_dir)
    pairs = [
        (settings.live_ranking_long_candidate, settings.live_truffle_long_qualification),
        (settings.live_ranking_short_candidate, settings.live_truffle_short_qualification),
    ]
    try:
        if args.sync:
            derived = derive_all(ranking_dir, data_dir, pairs)
            if args.dry_run:
                print(json.dumps(derived, indent=2, default=str))
                return
            for name, path in sync(knowledge_dir, derived):
                print(f"{name}: {path or 'unchanged'}")
        else:
            failed = False
            for identity in identities(ranking_dir, pairs):
                for number, problems in verify_ledger(knowledge_dir, identity, data_dir):
                    label = f"{identity['kind']}/{identity['id']} revision {number}"
                    print(f"{label}: {'VERIFIED' if not problems else 'INVALID'}")
                    for problem in problems:
                        print(f"  - {problem}")
                    failed |= bool(problems)
            sys.exit(1 if failed else 0)
    except (LedgerError, OSError, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
