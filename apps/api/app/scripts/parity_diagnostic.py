"""Live-vs-replay feature parity diagnostic (read-only; audit F-05).

For each of the most recent analyzed frames, compares what Sniffer stored
live (`sniffer_results`) with what `app.research.replay.replay_frame`
recomputes now, feature by feature, and prints mismatch rates. Under the
temporal contract (docs/TEMPORAL_CONTRACT.md) the two agree for frames
whose OI/funding inputs carry `ingested_at`; a frame measured before the
contract, or a candle corrected after its frame, can legitimately differ —
the diagnostic reports, it does not judge.

Reads frames, candles, OI/funding and `sniffer_results`; computes features
only, never forward outcomes, and writes nothing.

    python -m app.scripts.parity_diagnostic --frames 12
"""

import argparse
import asyncio
import json
from collections import Counter
from decimal import Decimal
from typing import Any

from sqlalchemy import exists, select

from app.db.session import get_session_factory
from app.models.frame import MarketFrame
from app.models.sniffer import SnifferResult
from app.repositories.sniffer_repository import SnifferRepository
from app.research.replay import replay_frame

#: `sniffer_results.value` is NUMERIC(30,12): compare at that precision.
_QUANTUM = Decimal("1e-12")


def _same(a: Decimal | None, b: Decimal | None) -> bool:
    if a is None or b is None:
        return a is b
    return a.quantize(_QUANTUM) == b.quantize(_QUANTUM)


async def diagnose(frames: int) -> dict[str, Any]:
    factory = get_session_factory()
    async with factory() as session:
        # Walk frames (small table) and probe results by their primary-key
        # prefix, never a DISTINCT over every stored value.
        times = list(
            (
                await session.execute(
                    select(MarketFrame.frame_time)
                    .where(exists().where(SnifferResult.frame_time == MarketFrame.frame_time))
                    .order_by(MarketFrame.frame_time.desc())
                    .limit(frames)
                )
            ).scalars()
        )
    compared: Counter[str] = Counter()
    mismatched: Counter[str] = Counter()
    per_frame = []
    for frame_time in sorted(times):
        async with factory() as session:
            live = await SnifferRepository(session).get_for_frame(frame_time)
            replayed = await replay_frame(session, frame_time)
        if live is None or replayed is None:
            continue
        again = {i.instrument_id: i.features for i in replayed.sniffs.instruments}
        frame_mismatches = 0
        for instrument in live.instruments:
            other = again.get(instrument.instrument_id)
            if other is None:
                continue
            for name, value in instrument.features.items():
                compared[name] += 1
                if not _same(value, other.get(name)):
                    mismatched[name] += 1
                    frame_mismatches += 1
        per_frame.append(
            {
                "frame_time": frame_time.isoformat(),
                "analysis": live.analysis,
                "mismatched_values": frame_mismatches,
            }
        )
    return {
        "frames": per_frame,
        "features": {
            name: {
                "compared": compared[name],
                "mismatched": mismatched[name],
                "rate": round(mismatched[name] / compared[name], 4) if compared[name] else None,
            }
            for name in sorted(compared)
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--frames", type=int, default=12, help="most recent analyzed frames")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(diagnose(args.frames)), indent=2))


if __name__ == "__main__":
    main()
