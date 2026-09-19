"""Raw shapes of Bybit's v5 REST responses.

Kept separate from the app's own domain model (`app.domain.market`) so a
Bybit field rename only ever touches this module.
"""

from pydantic import BaseModel


class BybitInstrument(BaseModel):
    symbol: str
    baseCoin: str
    quoteCoin: str
    settleCoin: str
    contractType: str
    status: str


class InstrumentsInfoResult(BaseModel):
    category: str
    list: list[BybitInstrument]
    nextPageCursor: str = ""
