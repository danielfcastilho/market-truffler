"""One-off maintenance: refill Open Interest gaps sealed in by the old
forward catch-up (before the chronological-catch-up fix, an outage longer
than ~16.7h left the older part of the gap permanently unfetched — see
`OpenInterestReconciler._catchup_step`).

For every active instrument, finds interior gaps in its stored series
(`OpenInterestRepository.find_gaps`) and refetches each gap oldest first,
in windows of under one page, with `fetch_open_interest_window` (which
follows the cursor rather than trusting a partial page). Only ever
upserts — nothing is deleted, existing rows are idempotently re-written
with Bybit's own values. A window Bybit returns nothing for is reported
as unrecoverable, never fabricated.

Bounded and polite: a fixed small concurrency and a delay between
requests, well under Bybit's public rate limit, alongside the live
reconcilers' own traffic. `--dry-run` only measures the gaps and
estimates the request count.

Usage (inside the api container):

    python -m app.scripts.repair_open_interest_gaps --dry-run
    python -m app.scripts.repair_open_interest_gaps
"""

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.integrations.bybit.client import BybitApiError, BybitClient
from app.repositories.instrument_repository import InstrumentRepository
from app.repositories.open_interest_repository import OpenInterestRepository
from app.services.open_interest_reconciler import (
    CATCHUP_WINDOW,
    _to_rows,
    fetch_open_interest_window,
)

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

_BUCKET = timedelta(minutes=5)
_CONCURRENCY = 3
_REQUEST_DELAY_SECONDS = 0.3


def _windows(gap_start: datetime, gap_end: datetime) -> list[tuple[datetime, datetime]]:
    """Chronological windows covering the open interval (gap_start, gap_end)."""
    windows = []
    start = gap_start + timedelta(milliseconds=1)
    while start < gap_end:
        end = min(gap_end - timedelta(milliseconds=1), start + CATCHUP_WINDOW)
        windows.append((start, end))
        start = end + timedelta(milliseconds=1)
    return windows


async def repair(dry_run: bool) -> dict[str, int]:
    settings = get_settings()
    session_factory = get_session_factory()
    client = BybitClient(
        base_url=settings.bybit_base_url, timeout_seconds=settings.bybit_timeout_seconds
    )
    async with session_factory() as session:
        instruments = await InstrumentRepository(session).list_active()

    totals = {
        "instruments": len(instruments),
        "instruments_with_gaps": 0,
        "gaps": 0,
        "missing_buckets_before": 0,
        "windows": 0,
        "buckets_recovered": 0,
        "windows_empty": 0,
        "windows_failed": 0,
    }
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def handle(instrument_id: int, symbol: str) -> None:
        async with session_factory() as session:
            gaps = await OpenInterestRepository(session).find_gaps(instrument_id, bucket=_BUCKET)
        if not gaps:
            return
        totals["instruments_with_gaps"] += 1
        totals["gaps"] += len(gaps)
        for gap_start, gap_end in gaps:
            totals["missing_buckets_before"] += int((gap_end - gap_start) / _BUCKET) - 1
            windows = _windows(gap_start, gap_end)
            totals["windows"] += len(windows)
            if dry_run:
                continue
            for start, end in windows:
                async with semaphore:
                    try:
                        result = await fetch_open_interest_window(client, symbol, start, end)
                    except BybitApiError as exc:
                        totals["windows_failed"] += 1
                        logger.warning(
                            "oi_gap_window_failed", extra={"symbol": symbol, "error": str(exc)}
                        )
                        continue
                    finally:
                        await asyncio.sleep(_REQUEST_DELAY_SECONDS)
                if not result.list:
                    totals["windows_empty"] += 1
                    continue
                async with session_factory() as session:
                    await OpenInterestRepository(session).upsert_many(
                        _to_rows(instrument_id, result)
                    )
                totals["buckets_recovered"] += len(result.list)

    started = datetime.now(UTC)
    done = 0
    for batch_start in range(0, len(instruments), 50):
        batch = instruments[batch_start : batch_start + 50]
        await asyncio.gather(*(handle(i.id, i.symbol) for i in batch))
        done += len(batch)
        logger.info(
            "oi_gap_repair_progress",
            extra={"done": done, "total": len(instruments), **totals},
        )
    totals["seconds"] = int((datetime.now(UTC) - started).total_seconds())
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="measure and estimate only")
    args = parser.parse_args()
    totals = asyncio.run(repair(args.dry_run))
    for key, value in totals.items():
        print(f"{key}: {value}")
    if args.dry_run:
        seconds = totals["windows"] * _REQUEST_DELAY_SECONDS / _CONCURRENCY
        print(f"estimated requests: ~{totals['windows']}, runtime: ~{seconds / 60:.0f} min")


if __name__ == "__main__":
    main()
