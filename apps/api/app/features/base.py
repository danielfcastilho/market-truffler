"""The contract every Sniffer feature implements.

Deliberately small: one named calculator, one async method taking a
finalized `MarketFrame` and a `CandleRepository` for any historical lookups
it needs, returning a value per instrument (or `None` — unavailable).

No registry, no dynamic discovery, no dependency graph. Adding metric #2
means writing one more file like `return_5m.py` and listing it in
`app.features.engine.FEATURES` — nothing else changes.
"""

from abc import ABC, abstractmethod
from decimal import Decimal

from app.domain.frame import MarketFrame
from app.repositories.candle_repository import CandleRepository


class Feature(ABC):
    """A single, named, frame-relative measurement.

    `calculate` must resolve any historical data it needs through
    set-oriented/batched repository calls for the *whole* frame at once —
    never a per-instrument query loop (M5 section 12) — and must derive its
    notion of "current" only from data already selected into `frame`
    (`frame.members[i].m1/m5/m15/h1`), never by querying `market_candles`
    for "the latest" candle independently (M5 section 13). Any historical
    comparison point must be computed as an exact time offset from that
    already-legal reference, so it can never reach past `frame.frame_time`.
    """

    name: str

    @abstractmethod
    async def calculate(
        self, frame: MarketFrame, candle_repo: CandleRepository
    ) -> dict[int, Decimal | None]:
        """Return `{instrument_id: value}` for every member of `frame`.

        A member this feature cannot evaluate (e.g. its required historical
        candle doesn't exist at the exact needed timestamp) still gets an
        entry, mapped to `None` — never silently dropped, never
        substituted with an approximate value.
        """
        raise NotImplementedError
