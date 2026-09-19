"""Domain model for 🐽 SNIFFER's measurements.

Sniffer turns a finalized Market Frame — "what was knowable at T" — into
per-instrument feature values — "what does that data measure as". These are
factual measurements, not judgments: nothing here ranks, scores, or decides
anything is desirable. See `app.features` for how a value is calculated and
`app.services.sniffer` for orchestration.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class SnifferInstrumentResult:
    """One instrument's feature measurements for one frame.

    `features` maps a feature name (e.g. `"return_5m"`) to its computed
    value, or `None` when that specific feature could not be computed for
    this instrument (e.g. the exact historical comparison candle it needs
    doesn't exist) — never omitted, never substituted, so a caller can tell
    "considered but unavailable" apart from "not analyzed at all".
    """

    instrument_id: int
    symbol: str
    features: dict[str, Decimal | None]


@dataclass(frozen=True)
class SnifferFrameResult:
    """Sniffer's analysis of one finalized Market Frame.

    Only ever built from `frame.members` — an instrument absent from the
    Market Frame (because M4 didn't have complete context for it at
    finalization) is never analyzed here, regardless of what
    `market_candles` might contain now (M5 section 8: Sniffer answers "what
    could have been known at T", not "what do we know now about T").
    """

    frame_time: datetime
    analyzed_at: datetime
    instruments: list[SnifferInstrumentResult]
