from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class MarketStatus(BaseModel):
    bybit_connectivity: Literal["ok", "down"]
    symbols_tracked: int | None
    market_data: Literal["ok", "down"]
    last_market_update: datetime | None
    data_freshness_seconds: float | None
    # Fraction in [0, 1] of the current active universe's promised rolling
    # history that is confirmed-reconciled right now — see
    # app.services.historical_coverage for exact semantics. None when it
    # can't be determined yet (e.g. no active instruments).
    historical_coverage: float | None
    # The most recently finalized (COMPLETE or PARTIAL) Market Frame — see
    # app.services.frame_synchronizer. None when no frame has finalized yet.
    latest_market_frame: datetime | None
    # That frame's available_instruments / expected_instruments, in [0, 1].
    # None alongside latest_market_frame=None.
    frame_completeness: float | None
    # Per-symbol historical-data readiness (DISCOVERED -> BACKFILLING ->
    # READY) across the active universe right now — see
    # app.services.symbol_readiness for exact semantics. Counts always sum
    # to symbols_tracked; never None (an empty universe is legitimately
    # 0/0/0, not "unknown").
    symbols_discovered: int
    symbols_backfilling: int
    symbols_ready: int
