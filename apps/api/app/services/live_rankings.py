"""Experimental live Long/Short rankings: the configured ranking candidates
applied to Sniffer's latest analyzed frame, through the one shared scoring
implementation (`app.ranking.scoring`) that OINK CORP's historical ranking
research also uses.

Read-only and point-in-time: it reads only that frame's Sniffer features
(never a dataset, an outcome label or anything reserved for prospective
evaluation) and computes on request — nothing is persisted. Long and Short
always come from the same frame. A frame that hasn't finalized, a missing
candidate, or too small a cross-section yields an explicit unavailable
state; nothing is fabricated or substituted.

Freshness uses existing conventions: frames are built every
`BASE_DURATION` and should finalize within `FINALIZE_ALLOWANCE`, so a
ranking is stale once the next frame should have finalized, or when a
newer frame finalized more than `FINALIZE_ALLOWANCE` ago and Sniffer still
hasn't analyzed it (it normally does within seconds).
"""

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.frame import FrameStatus
from app.domain.timeframes import BASE_DURATION
from app.ranking.scoring import (
    MIN_CROSS_SECTION,
    RankingCandidate,
    candidate_hash,
    eligible_indices,
    load_candidate_file,
    score_cross_section,
)
from app.repositories.frame_repository import FrameRepository
from app.repositories.sniffer_repository import SnifferRepository
from app.services.collection_continuity import FINALIZE_ALLOWANCE

#: Factual context shown next to each ranked coin (Sniffer feature names).
CONTEXT_FEATURES = ("return_1h", "oi_change_1h", "relative_volume_1h")
VALIDATION_STATUS = (
    "experimental — manually specified, not optimized, not independently validated, not a Champion"
)


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def load_live_candidate(candidates_dir: Path, candidate_id: str) -> RankingCandidate:
    """The configured candidate, or ValueError — never a substitute."""
    try:
        candidate = load_candidate_file(candidates_dir / f"{candidate_id}.json")
    except OSError as exc:
        raise ValueError(f"candidate {candidate_id} not found") from exc
    except ValidationError as exc:
        raise ValueError(
            f"candidate {candidate_id} is invalid: {exc.error_count()} errors"
        ) from exc
    if candidate.candidate_id != candidate_id:
        raise ValueError(f"{candidate_id}.json declares {candidate.candidate_id!r}")
    return candidate


def rank_direction(
    candidate_id: str,
    direction: str,
    candidates_dir: Path,
    symbols: list[str],
    features: dict[str, list[Any]],
) -> dict[str, Any]:
    """One direction's ranking of one frame's instruments."""
    try:
        candidate = load_live_candidate(candidates_dir, candidate_id)
    except ValueError as exc:
        return {"direction": direction, "available": False, "reason": str(exc), "rows": []}
    model = {
        "candidate_id": candidate.candidate_id,
        "name": candidate.name,
        "spec_hash": candidate_hash(candidate),
        "model_family": candidate.model_family,
        "direction": candidate.direction,
        "objective": candidate.objective,
        "terms": [t.model_dump() for t in candidate.terms],
        "missing_policy": candidate.missing_policy,
        "validation_status": VALIDATION_STATUS,
    }
    if candidate.direction != direction:
        return {
            "direction": direction,
            "available": False,
            "reason": f"candidate {candidate_id} ranks {candidate.direction}, not {direction}",
            "model": model,
            "rows": [],
        }
    missing = {f for f in candidate.features if f not in features}
    if missing:
        return {
            "direction": direction,
            "available": False,
            "reason": f"Sniffer does not compute {sorted(missing)}",
            "model": model,
            "rows": [],
        }
    eligible = eligible_indices({f: features[f] for f in candidate.features}, candidate.features)
    eligible_set = set(eligible)
    excluded = [
        {
            "symbol": symbols[i],
            "missing": [f for f in candidate.features if features[f][i] is None],
        }
        for i in range(len(symbols))
        if i not in eligible_set
    ]
    base = {
        "direction": direction,
        "model": model,
        "eligible": len(eligible),
        "excluded": excluded,
    }
    if len(eligible) < MIN_CROSS_SECTION:
        return {
            **base,
            "available": False,
            "reason": f"{len(eligible)} eligible instruments (< {MIN_CROSS_SECTION})",
            "rows": [],
        }
    ranked_symbols = [symbols[i] for i in eligible]
    scored = score_cross_section(
        candidate,
        ranked_symbols,
        {f: [features[f][i] for i in eligible] for f in candidate.features},
    )
    rows = []
    for rank, position in enumerate(scored.order, start=1):
        source = eligible[position]
        rows.append(
            {
                "rank": rank,
                "symbol": ranked_symbols[position],
                "score": scored.scores[position],
                "contributions": [
                    {
                        "feature": t.feature,
                        "transform": t.transform,
                        "weight": t.weight,
                        "raw": t.raw[position],
                        "transformed": t.transformed[position],
                        "contribution": t.contribution[position],
                    }
                    for t in scored.terms
                ],
                "context": {
                    f: features[f][source] if f in features else None for f in CONTEXT_FEATURES
                },
            }
        )
    return {
        **base,
        "available": True,
        "reason": None,
        "degenerate_features": scored.degenerate_features,
        "rows": rows,
    }


async def live_rankings(
    session: AsyncSession,
    *,
    candidates_dir: Path,
    long_candidate: str,
    short_candidate: str,
    now: datetime,
) -> dict[str, Any]:
    sniffer = await SnifferRepository(session).get_latest()
    if sniffer is None:
        return {"available": False, "reason": "Sniffer has not analyzed any frame yet."}
    frames = FrameRepository(session)
    frame = await frames.get_summary(sniffer.frame_time)
    if frame is None or frame.status == FrameStatus.BUILDING:
        return {
            "available": False,
            "reason": f"frame {sniffer.frame_time.isoformat()} has not finalized; not ranking it",
        }
    latest = await frames.get_latest_finalized_summary()
    due = sniffer.frame_time + BASE_DURATION + FINALIZE_ALLOWANCE
    stale_reason = None
    # Sniffer analyzes a frame seconds after it finalizes; only once the
    # finalization allowance has passed is an unanalyzed newer frame a lag.
    if (
        latest is not None
        and latest.frame_time > sniffer.frame_time
        and latest.finalized_at is not None
        and now > latest.finalized_at + FINALIZE_ALLOWANCE
    ):
        stale_reason = (
            f"a newer frame ({latest.frame_time.isoformat()}) finalized but Sniffer has not "
            "analyzed it"
        )
    elif now > due:
        stale_reason = f"the next frame should have finalized by {due.isoformat()}"

    instruments = sorted(sniffer.instruments, key=lambda i: i.symbol)
    symbols = [i.symbol for i in instruments]
    names = {name for i in instruments for name in i.features}
    features = {name: [_float(i.features.get(name)) for i in instruments] for name in names}
    return {
        "available": True,
        "reason": None,
        "frame": {
            "frame_time": sniffer.frame_time,
            "status": frame.status.value,
            "completeness": frame.completeness,
            "expected_instruments": frame.expected_instruments,
            "available_instruments": frame.available_instruments,
            "analyzed_instruments": len(instruments),
            "analyzed_at": sniffer.analyzed_at,
            "age_seconds": (now - sniffer.frame_time).total_seconds(),
            "stale": stale_reason is not None,
            "stale_reason": stale_reason,
        },
        "long": rank_direction(long_candidate, "long", candidates_dir, symbols, features),
        "short": rank_direction(short_candidate, "short", candidates_dir, symbols, features),
    }
