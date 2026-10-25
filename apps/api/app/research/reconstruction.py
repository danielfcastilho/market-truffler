"""OINK CORP historical reconstruction: research-only observations for
5-minute boundaries that have no persisted live Market Frame.

A live frame is a statement of what MARKET actually had at T. Frames were
not collected while the app was down, but the reconcilers backfilled the
raw inputs afterwards (candles, Open Interest, funding). This module builds
an *in-memory* `MarketFrame` for such a T from that raw data and measures
it with the unchanged production `FeatureEngine` — nothing is persisted,
nothing touches `market_frames`, `market_frame_members` or
`sniffer_results`, and the result is never presented as a live frame
(datasets built from it carry `observation_source: "reconstructed"`, see
`app.research.dataset`).

UNIVERSE (`UNIVERSE_RULE`, version `RECONSTRUCTION_VERSION`). The database
has no historical listing calendar: `instruments.first_seen_at` /
`last_seen_at` only record when *this app* saw an instrument, which during
outages is not when it was listed or delisted. So membership is derived
from the data itself: an instrument is in the universe at T when its 5m
candle closing exactly at T exists (it was trading at T). Listings enter
with their first backfilled candle; an instrument stops counting when its
candles stop. Limits, disclosed in every manifest:

- only instruments this app has ever discovered (`instruments`, first
  populated 2026-09-19) can appear: anything listed *and* delisted
  entirely inside an outage, or delisted before 2026-09-19, is absent;
- an instrument delisted during an outage has candles only up to the last
  time the app saw it (inactive instruments are not backfilled), so it
  leaves the reconstructed universe early — survivorship-biased towards
  coins that are still listed;
- live frames additionally admit an instrument whose latest 5m candle is
  older than T (a recent gap); reconstruction does not (its label would
  be undefined anyway, see `app.research.forward_returns`).

ANCHORS. Exactly the live rule (`app.services.frame_synchronizer`): per
timeframe, the latest candle closed by T
(`CandleRepository.fetch_latest_closed_per_instrument`); a member needs
all `GATING_TIMEFRAMES`; 4h/24h are optional context. The query is bounded
below by `ANCHOR_LOOKBACK` (live is unbounded): an anchor older than that
would be stale context, and the bound keeps each frame's queries cheap.

TEMPORAL CONTRACT. Features run unchanged, so Open Interest and funding
keep the contract's availability rule (`ingested_at <= decision_time(T)`,
NULL accepted as legacy/unverified). For reconstructed T this is a
property of the data, not a claim: rows backfilled under the contract
after an outage carry a later `ingested_at` and are *excluded*, so such
features are missing rather than taken from data the system did not have.
Candles have no availability record — reconstructed candles are what the
exchange reports now, which a live frame at T may not have had.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.frame import FrameStatus, MarketFrame, MarketFrameMember
from app.domain.timeframes import BASE_DURATION, TIMEFRAMES, is_aligned
from app.features.engine import FeatureEngine
from app.models.instrument import Instrument
from app.repositories.candle_repository import CandleRepository
from app.repositories.frame_repository import _to_context
from app.repositories.funding_rate_repository import FundingRateRepository
from app.repositories.open_interest_repository import OpenInterestRepository
from app.research.replay import ReplayResult
from app.services.frame_synchronizer import GATING_TIMEFRAMES

RECONSTRUCTION_VERSION = 1
UNIVERSE_RULE = "candle_presence_v1"
#: How far before its timeframe's latest possible anchor a candle may be
#: and still be selected (live has no bound — see module docstring).
ANCHOR_LOOKBACK = timedelta(days=2)

UNIVERSE_NOTE = (
    "Reconstructed universe (candle_presence_v1): an instrument is a member at T when its 5m "
    "candle closing exactly at T exists in today's database, plus the live frame rule's legal "
    "15m/1h candles. There is no historical listing calendar: only instruments this app ever "
    "discovered (since 2026-09-19) can appear, and an instrument delisted during an outage "
    "leaves the universe when the app last saw it — potentially survivorship-biased."
)
LIMITATIONS = (
    "Reconstructed research observations, not live frames: nothing here was available to the "
    "system at frame_time as a frame; candles are what the exchange reports now.",
    "Exploratory research only: never part of a prospective evaluation.",
    "Universe derived from candle presence (see universe_note); delistings during outages are "
    "only approximately reflected.",
    "Open Interest/funding keep the temporal contract's availability rule: values backfilled "
    "under it after an outage are excluded (missing), not used.",
)


@dataclass(frozen=True)
class ReconstructedFrame:
    result: ReplayResult
    #: Instruments with a 5m candle closing exactly at T (the universe).
    trading: int


async def reconstruct_frame(session: AsyncSession, frame_time: datetime) -> ReconstructedFrame:
    """The research-only frame for `frame_time`, measured by the unchanged
    `FeatureEngine`. Raises `ValueError` off a 5-minute boundary."""
    if not is_aligned(frame_time):
        raise ValueError(f"frame_time {frame_time.isoformat()} is not a 5m boundary")
    instruments = dict(
        (await session.execute(select(Instrument.id, Instrument.symbol))).tuples().all()
    )
    candles = CandleRepository(session)
    exact_5m = frame_time - BASE_DURATION
    trading = await candles.fetch_latest_closed_per_instrument(
        "5m", list(instruments), exact_5m, min_open_time=exact_5m
    )
    ids = sorted(trading)
    anchors = {"5m": trading}
    for timeframe, duration in TIMEFRAMES.items():
        if timeframe == "5m":
            continue
        latest = frame_time - duration
        anchors[timeframe] = await candles.fetch_latest_closed_per_instrument(
            timeframe, ids, latest, min_open_time=latest - ANCHOR_LOOKBACK
        )
    members = []
    for instrument_id in ids:
        if any(instrument_id not in anchors[tf] for tf in GATING_TIMEFRAMES):
            continue
        h4 = anchors["4h"].get(instrument_id)
        h24 = anchors["24h"].get(instrument_id)
        members.append(
            MarketFrameMember(
                instrument_id=instrument_id,
                symbol=instruments[instrument_id],
                m5=_to_context(anchors["5m"][instrument_id]),
                m15=_to_context(anchors["15m"][instrument_id]),
                h1=_to_context(anchors["1h"][instrument_id]),
                h4=_to_context(h4) if h4 is not None else None,
                h24=_to_context(h24) if h24 is not None else None,
            )
        )
    now = datetime.now(UTC)
    frame = MarketFrame(
        frame_time=frame_time,
        expected_instruments=len(ids),
        available_instruments=len(members),
        status=FrameStatus.COMPLETE if len(members) == len(ids) else FrameStatus.PARTIAL,
        created_at=now,
        finalized_at=None,
        members=members,
    )
    sniffs = await FeatureEngine().run(
        frame,
        candles,
        OpenInterestRepository(session),
        FundingRateRepository(session),
        analyzed_at=now,
    )
    return ReconstructedFrame(ReplayResult(frame=frame, sniffs=sniffs), trading=len(ids))


def _same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is b
    x, y = float(a), float(b)
    return x == y or abs(x - y) <= 1e-12 * max(1.0, abs(x), abs(y))


async def compare_with_live(session: AsyncSession, frame_time: datetime) -> dict | None:
    """Reconstruction against the persisted live frame at `frame_time`
    (replayed with today's engine, `app.research.replay`): universe,
    anchors and every feature value. None when no live frame exists.
    Diagnostic only — reads, never writes."""
    from app.research.replay import replay_frame

    live = await replay_frame(session, frame_time)
    if live is None:
        return None
    recon = (await reconstruct_frame(session, frame_time)).result
    live_members = {m.instrument_id: m for m in live.frame.members}
    recon_members = {m.instrument_id: m for m in recon.frame.members}
    shared = sorted(set(live_members) & set(recon_members))
    anchor_diffs: dict[str, int] = {}
    for i in shared:
        for tf in ("m5", "m15", "h1", "h4", "h24"):
            a, b = getattr(live_members[i], tf), getattr(recon_members[i], tf)
            if (a and a.open_time) != (b and b.open_time) or (a and b and a.close != b.close):
                anchor_diffs[tf] = anchor_diffs.get(tf, 0) + 1
    live_f = {s.instrument_id: s.features for s in live.sniffs.instruments}
    recon_f = {s.instrument_id: s.features for s in recon.sniffs.instruments}
    feature_diffs: dict[str, dict[str, int]] = {}
    for i in shared:
        for name, value in live_f[i].items():
            other = recon_f[i].get(name)
            if _same(value, other):
                continue
            kind = (
                "live_only" if other is None else "recon_only" if value is None else "different"
            )
            feature_diffs.setdefault(name, {}).setdefault(kind, 0)
            feature_diffs[name][kind] += 1
    return {
        "frame_time": frame_time.isoformat(),
        "live_members": len(live_members),
        "reconstructed_members": len(recon_members),
        "only_live": sorted(live_members[i].symbol for i in set(live_members) - set(recon_members)),
        "only_reconstructed": sorted(
            recon_members[i].symbol for i in set(recon_members) - set(live_members)
        ),
        "shared": len(shared),
        "anchor_differences": anchor_diffs,
        "feature_differences": feature_diffs,
    }


__all__ = [
    "ANCHOR_LOOKBACK",
    "LIMITATIONS",
    "RECONSTRUCTION_VERSION",
    "UNIVERSE_NOTE",
    "UNIVERSE_RULE",
    "ReconstructedFrame",
    "compare_with_live",
    "reconstruct_frame",
]
