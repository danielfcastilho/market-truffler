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
    #: Backend order: rank 1 = most attractive in this direction.
    rows: list[dict[str, Any]]


class LiveRankings(BaseModel):
    available: bool
    reason: str | None
    frame: RankingFrame | None = None
    long: DirectionRanking | None = None
    short: DirectionRanking | None = None
