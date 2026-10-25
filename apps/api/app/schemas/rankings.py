"""Live Sniffer rankings (see `app.services.live_rankings`)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class RankingFrame(BaseModel):
    frame_time: datetime
    status: str
    completeness: float
    expected_instruments: int
    available_instruments: int
    analyzed_instruments: int
    analyzed_at: datetime
    # "live" | "recovery" | None (before analysis provenance was recorded).
    analysis: str | None = None
    #: 2 for results carrying analysis provenance, 1 for older ones.
    temporal_contract_version: int | None = None
    age_seconds: float
    stale: bool
    stale_reason: str | None


class DirectionRanking(BaseModel):
    direction: str
    available: bool
    reason: str | None
    #: The registered candidate (None when it couldn't be loaded).
    model: dict[str, Any] | None = None
    eligible: int | None = None
    #: Instruments outside the decision universe, with the features they lack.
    excluded: list[dict[str, Any]] = []
    degenerate_features: list[str] = []
    #: Backend order: rank 1 = most attractive in this direction. Each row
    #: carries `qualification` ({qualified, failed, missing}; with `checks`
    #: on a symbol slice), or None when qualification is withheld.
    rows: list[dict[str, Any]]
    #: Descriptive score distribution, separation and stability
    #: (`app.ranking.diagnostics`) — not opportunity quality.
    diagnostics: dict[str, Any] | None = None
    #: Experimental Truffle qualification (`app.ranking.qualification`):
    #: status "evaluated" (possibly zero Truffles), "withheld" (stale or
    #: incomplete frame) or "unavailable".
    qualification: dict[str, Any] | None = None


class LiveRankings(BaseModel):
    available: bool
    reason: str | None
    frame: RankingFrame | None = None
    long: DirectionRanking | None = None
    short: DirectionRanking | None = None
    #: Symbols that are Truffles for Long and for Short in this frame.
    truffles_in_both_directions: list[str] = []
