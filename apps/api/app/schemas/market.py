from typing import Literal

from pydantic import BaseModel


class MarketStatus(BaseModel):
    bybit_connectivity: Literal["ok", "down"]
    symbols_tracked: int | None
