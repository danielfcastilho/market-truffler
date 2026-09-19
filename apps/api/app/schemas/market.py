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
