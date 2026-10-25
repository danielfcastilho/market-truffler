"""OINK CORP: generate a historical Sniff-replay + forward-outcome research
dataset (Parquet + manifest.json) — the first real OINK CORP capability
(see `docs/ARCHITECTURE.md`'s OINK CORP section and `app.research.*`).

A read-only batch job: it never mutates production data (read-only
against `market_frames`/`market_candles`/`open_interest_observations`/
`funding_rate_observations`) and never writes to `sniffer_results`. Its
output is only reproducible while those sources still exist — frames
exist only for app uptime and everything is pruned by retention — so a
dataset is a permanent record, not a cache (see `docs/ARCHITECTURE.md`).

No resumability by design: an interrupted run leaves nothing behind and is
simply re-run while its sources still exist.

Usage (inside the api container):

    python -m app.scripts.generate_research_dataset \\
        --start 2026-09-01T00:00:00Z --end 2026-09-27T00:00:00Z \\
        --sample-interval-minutes 15 --horizons 15m,1h,4h,24h \\
        --output research_v1

`--sample-interval-minutes` and every `--horizons` entry must be a
multiple of 5 minutes (the 5m base timeframe); anything else is rejected
up front, never rounded.

Never overwrites an existing dataset (a permanent research record whose
sources may have expired). Verify one later — sha256 + version checks —
with:

    python -m app.scripts.generate_research_dataset --verify research_v1

Research-only reconstruction for times without persisted live frames
(`app.research.reconstruction` — exploratory only, never pooled with
live-observed datasets, never read by a prospective evaluation):

    python -m app.scripts.generate_research_dataset --reconstruct \
        --start 2026-09-28T00:00:00Z --end 2026-09-29T00:00:00Z \
        --sample-interval-minutes 15 --horizons 15m,1h,4h --output recon_s15m_20260928

Writes to `<Settings.research_data_dir>/<output>/` inside the container —
durable only because `docker-compose.yml` bind-mounts that path to
`research/oink_corp/data` on the host.
"""

import argparse
import asyncio
import logging
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.config import get_settings
from app.db.session import get_engine, get_session_factory
from app.research.continuity import GenerationLock, GenerationLockLost, wait_for_live_window
from app.research.dataset import generate_dataset, read_dataset, validate_sample_interval
from app.research.forward_returns import validate_horizons
from app.research.reservations import ReservedDataError, check_generation_window

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_DEFAULT_HORIZONS = "15m,1h,4h,24h"

_UNIT_MINUTES = {"m": 1, "h": 60, "d": 60 * 24}
_LABEL_RE = re.compile(r"^(\d+)([mhd])$")


#: Every reconstructed dataset's name starts with this, so it can never be
#: mistaken for a live-observed one (`app.research.reconstruction`).
RECONSTRUCTED_PREFIX = "recon_"


def _parse_horizon(label: str) -> timedelta:
    match = _LABEL_RE.match(label.strip())
    if not match:
        raise argparse.ArgumentTypeError(
            f"invalid horizon label {label!r} — expected e.g. '15m', '1h', '4h', '24h'"
        )
    quantity, unit = match.groups()
    return timedelta(minutes=int(quantity) * _UNIT_MINUTES[unit])


def _parse_horizons(raw: str) -> dict[str, timedelta]:
    labels = [label.strip() for label in raw.split(",") if label.strip()]
    if not labels:
        raise argparse.ArgumentTypeError("--horizons must list at least one label")
    horizons = {label: _parse_horizon(label) for label in labels}
    try:
        validate_horizons(horizons)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return horizons


def _parse_sample_interval(raw: str) -> int:
    try:
        interval = int(raw)
        validate_sample_interval(interval)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return interval


def _parse_timestamp(raw: str) -> datetime:
    value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--start", type=_parse_timestamp, help="ISO 8601, e.g. 2026-09-01T00:00:00Z"
    )
    parser.add_argument("--end", type=_parse_timestamp, help="ISO 8601, exclusive")
    parser.add_argument("--sample-interval-minutes", type=_parse_sample_interval, default=15)
    parser.add_argument(
        "--horizons", type=_parse_horizons, default=_parse_horizons(_DEFAULT_HORIZONS)
    )
    parser.add_argument("--output", help="dataset directory name, e.g. research_v1")
    parser.add_argument(
        "--reconstruct",
        action="store_true",
        help="research-only: reconstruct every grid point from raw candles/OI/funding instead "
        "of persisted live frames (app.research.reconstruction); --output must start with "
        f"{RECONSTRUCTED_PREFIX!r}",
    )
    parser.add_argument(
        "--verify",
        metavar="NAME",
        help="verify an existing dataset's sha256 and versions instead of generating one",
    )
    return parser


async def _run(args: argparse.Namespace) -> None:
    settings = get_settings()
    if args.reconstruct != args.output.startswith(RECONSTRUCTED_PREFIX):
        raise RuntimeError(
            f"reconstructed datasets, and only they, are named {RECONSTRUCTED_PREFIX}*"
        )
    output_dir = Path(settings.research_data_dir) / args.output
    # Before touching any data: generating labels over reserved days would
    # read protected outcomes.
    check_generation_window(
        Path(settings.research_data_dir), args.start, args.end + max(args.horizons.values())
    )

    logger.info(
        "research_dataset_starting",
        extra={
            "start": args.start.isoformat(),
            "end": args.end.isoformat(),
            "sample_interval_minutes": args.sample_interval_minutes,
            "horizons": list(args.horizons),
            "output_dir": str(output_dir),
        },
    )

    def _on_progress(done: int, total: int) -> None:
        if total == 0 or done % 20 == 0 or done == total:
            logger.info("research_dataset_progress", extra={"done": done, "total": total})

    # The same lock the continuity worker holds: never two generators at once.
    lock = GenerationLock(get_engine())
    if not await lock.acquire():
        raise RuntimeError("another research dataset generation is running (lock held)")
    try:
        manifest = await generate_dataset(
            get_session_factory(),
            start=args.start,
            end=args.end,
            interval_minutes=args.sample_interval_minutes,
            horizons=args.horizons,
            output_dir=output_dir,
            on_progress=_on_progress,
            before_publish=lock.ensure_held,
            reconstruct=args.reconstruct,
            # Reconstruction runs for hours: yield to live frame work, like
            # the continuity worker.
            before_frame=wait_for_live_window if args.reconstruct else None,
        )
    finally:
        await lock.release()

    logger.info(
        "research_dataset_complete",
        extra={
            "output_dir": str(output_dir),
            "row_count": manifest["row_count"],
            "frame_times_processed": manifest["frame_times_processed"],
            "frame_times_missing": manifest["frame_times_missing"],
        },
    )


def _verify(name: str) -> None:
    dataset_dir = Path(get_settings().research_data_dir) / name
    manifest, table = read_dataset(dataset_dir)
    print(
        f"OK {dataset_dir}: sha256 {manifest['data_sha256']}, {table.num_rows} rows, "
        f"{manifest['first_frame_time']} .. {manifest['last_frame_time']}"
    )


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.verify:
        try:
            _verify(args.verify)
        except (OSError, ValueError) as exc:
            print(f"FAILED: {exc}", file=sys.stderr)
            sys.exit(1)
        return
    if args.start is None or args.end is None or args.output is None:
        parser.error("--start, --end and --output are required unless --verify is given")
    if args.end <= args.start:
        print("--end must be after --start", file=sys.stderr)
        sys.exit(1)
    try:
        asyncio.run(_run(args))
    except (FileExistsError, RuntimeError, ReservedDataError, GenerationLockLost) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
