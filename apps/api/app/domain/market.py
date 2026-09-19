"""Application-level market domain model — independent of any exchange's wire format."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    """A single tradable instrument in Market Truffler's market universe."""

    symbol: str
    base_coin: str
    quote_coin: str
