"""OINK CORP's historical Sniff replay.

Reconstructs what **today's** production `FeatureEngine` would measure at a
historical `frame_time`, using only data knowable at that instant. This is a
deliberately narrow claim — it answers "what would current Sniffer measure
at T", never "what did the historical version of the software actually
compute at T". Feature definitions evolve (new families get added, bugs get
fixed); replay always runs whatever `FEATURES`/formulas are live in this
codebase right now, against the exact anchors that were legally knowable at
T. There is no historical code-version execution here, and none is planned —
a dataset's producing code version stays traceable instead via its manifest
(git commit + the exact `FEATURES` name list captured at generation time; see
`app.research.dataset`).

Reuses `FrameRepository.get_frame` and `FeatureEngine.run` completely
unchanged — byte-for-byte the same two calls `app.services.sniffer.Sniffer.
analyze_frame` makes for "now", just against a `frame_time` in the past.
This is what makes production-vs-research parity a structural property of
the code path rather than a maintained illusion: see `test_replay.py`'s
parity test, which runs both and asserts identical output. Mirrors
`Sniffer.analyze_frame`'s own BUILDING-skip rule for the same reason: a
frame still under construction has no legally-finalized "what was knowable"
answer yet.

Only 5-minute-boundary `frame_time`s can be replayed (`ValueError`
otherwise): those are the only instants at which a base-timeframe candle
has just closed, and the only ones MARKET builds frames at. Frames persisted
at other minutes before 5m became the base timeframe are deliberately not
replayable.

PERFORMANCE NOTE: every `replay_frame` call pays `FrameRepository.
get_frame`'s full cost (one multi-timeframe join over `market_candles`;
see that method). This package intentionally reuses that read unchanged
for parity rather than introducing a faster, parallel read path that could
silently drift from what `get_frame` actually returns.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.frame import FrameStatus, MarketFrame
from app.domain.sniffer import SnifferFrameResult
from app.domain.timeframes import BASE_TIMEFRAME, is_aligned
from app.features.engine import FeatureEngine
from app.repositories.candle_repository import CandleRepository
from app.repositories.frame_repository import FrameRepository
from app.repositories.funding_rate_repository import FundingRateRepository
from app.repositories.open_interest_repository import OpenInterestRepository


@dataclass(frozen=True)
class ReplayResult:
    """A historical frame plus the Sniffs replayed against it — bundled
    together because `app.research.forward_returns` needs `frame.members`
    (for T-anchors and the cross-sectional universe) alongside `sniffs`
    (the Sniff values themselves), and re-fetching the frame a second time
    would be wasteful given how expensive that read already is."""

    frame: MarketFrame
    sniffs: SnifferFrameResult


async def replay_frame(session: AsyncSession, frame_time: datetime) -> ReplayResult | None:
    """Replay one historical `frame_time`, or `None` if there's nothing to
    replay: no `MarketFrame` was ever persisted for it, or it's still
    BUILDING (mirrors `Sniffer.analyze_frame`'s own skip rule exactly —
    whatever Sniffer itself would decline to analyze, replay declines too).

    Raises `ValueError` if `frame_time` isn't a 5-minute boundary."""
    if not is_aligned(frame_time):
        raise ValueError(f"frame_time {frame_time.isoformat()} is not a {BASE_TIMEFRAME} boundary")
    frame = await FrameRepository(session).get_frame(frame_time)
    if frame is None or frame.status == FrameStatus.BUILDING:
        return None

    sniffs = await FeatureEngine().run(
        frame,
        CandleRepository(session),
        OpenInterestRepository(session),
        FundingRateRepository(session),
        analyzed_at=datetime.now(UTC),
    )
    return ReplayResult(frame=frame, sniffs=sniffs)
