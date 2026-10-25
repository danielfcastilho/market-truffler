"""Experimental live Long/Short rankings: the configured ranking candidates
applied to Sniffer's latest analyzed frame, through the one shared scoring
implementation (`app.ranking.scoring`) that OINK CORP's historical ranking
research also uses.

Read-only and point-in-time: it reads only that frame's Sniffer features
(never a dataset, an outcome label or anything reserved for prospective
evaluation). Nothing is persisted: the clock-independent part of a
response — both rankings, their diagnostics (`app.ranking.diagnostics`)
and Truffle qualification checks (`app.ranking.qualification`) — is
computed once per analyzed frame and kept in a small in-process cache;
freshness and the qualification frame gates are applied per request, so a
stale snapshot never shows Truffles. Diagnostics' stability compares with
the frame one boundary earlier, re-ranked by the same candidate spec from
its stored features (only the features the candidate uses are loaded). Long and Short
always come from the same frame. A frame that hasn't finalized, a missing
candidate, or too small a cross-section yields an explicit unavailable
state; nothing is fabricated or substituted.

Freshness uses existing conventions: frames are built every
`BASE_DURATION` and should finalize within `FINALIZE_ALLOWANCE`, so a
ranking is stale once the next frame should have finalized, or when a
newer frame finalized more than `FINALIZE_ALLOWANCE` ago and Sniffer still
hasn't analyzed it (it normally does within seconds).
"""

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.frame import FrameStatus
from app.domain.temporal import TEMPORAL_CONTRACT_VERSION
from app.domain.timeframes import BASE_DURATION
from app.ranking import diagnostics
from app.ranking.qualification import evaluate, load_qualification, qualified, spec_summary
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
DIAGNOSTIC_LIMITATIONS = (
    "Descriptive only: how this model's scores are spread in this frame, not opportunity "
    "quality or expected profitability.",
    "Scores are sums of within-frame percentiles, so their range is fixed by construction; "
    "separation mostly reflects how the model's features co-move in this frame.",
    "Stability compares with the previous frame ranked by the same candidate spec from its "
    "stored features; it describes turnover, not reliability.",
)
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


#: Computed snapshots, keyed by everything they depend on (the analyzed
#: frame and the spec files' bytes): diagnostics and qualification run once
#: per analyzed frame, not per request. Small and bounded.
_SNAPSHOTS: dict[tuple[Any, ...], dict[str, Any]] = {}
_MAX_SNAPSHOTS = 4


def _file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "missing"


def _contract_version(analysis: str | None) -> int:
    """Results carry `analysis` from temporal contract v2 on; NULL ones were
    measured before it (v1)."""
    return TEMPORAL_CONTRACT_VERSION if analysis is not None else 1


async def live_rankings(
    session: AsyncSession,
    *,
    candidates_dir: Path,
    long_candidate: str,
    short_candidate: str,
    now: datetime,
    symbol: str | None = None,
    qualifications_dir: Path | None = None,
    long_qualification: str | None = None,
    short_qualification: str | None = None,
) -> dict[str, Any]:
    repo = SnifferRepository(session)
    marker = await repo.latest_marker()
    if marker is None:
        return {"available": False, "reason": "Sniffer has not analyzed any frame yet."}
    frame_time = marker[0]
    frames = FrameRepository(session)
    frame = await frames.get_summary(frame_time)
    if frame is None or frame.status == FrameStatus.BUILDING:
        return {
            "available": False,
            "reason": f"frame {frame_time.isoformat()} has not finalized; not ranking it",
        }
    latest = await frames.get_latest_finalized_summary()
    due = frame_time + BASE_DURATION + FINALIZE_ALLOWANCE
    stale_reason = None
    # Sniffer analyzes a frame seconds after it finalizes; only once the
    # finalization allowance has passed is an unanalyzed newer frame a lag.
    if (
        latest is not None
        and latest.frame_time > frame_time
        and latest.finalized_at is not None
        and now > latest.finalized_at + FINALIZE_ALLOWANCE
    ):
        stale_reason = (
            f"a newer frame ({latest.frame_time.isoformat()}) finalized but Sniffer has not "
            "analyzed it"
        )
    elif now > due:
        stale_reason = f"the next frame should have finalized by {due.isoformat()}"

    configured = {
        "long": (long_candidate, long_qualification),
        "short": (short_candidate, short_qualification),
    }
    key = (
        marker,
        str(candidates_dir),
        str(qualifications_dir),
        tuple(
            (
                c,
                _file_digest(candidates_dir / f"{c}.json"),
                q,
                _file_digest(qualifications_dir / f"{q}.json")
                if qualifications_dir and q
                else None,
            )
            for c, q in configured.values()
        ),
    )
    snapshot = _SNAPSHOTS.get(key)
    if snapshot is None:
        snapshot = await _snapshot(repo, frame_time, candidates_dir, qualifications_dir, configured)
        if len(_SNAPSHOTS) >= _MAX_SNAPSHOTS:
            _SNAPSHOTS.pop(next(iter(_SNAPSHOTS)))
        _SNAPSHOTS[key] = snapshot

    result: dict[str, Any] = {
        "available": True,
        "reason": None,
        "frame": {
            **snapshot["frame"],
            "status": frame.status.value,
            "completeness": frame.completeness,
            "expected_instruments": frame.expected_instruments,
            "available_instruments": frame.available_instruments,
            "age_seconds": (now - frame_time).total_seconds(),
            "stale": stale_reason is not None,
            "stale_reason": stale_reason,
        },
    }
    for direction in ("long", "short"):
        ranking = _gate(snapshot[direction], stale_reason, frame.completeness)
        result[direction] = _only(ranking, symbol, snapshot["checks"][direction])
    result["truffles_in_both_directions"] = sorted(
        {t["symbol"] for t in result["long"]["qualification"]["truffles"]}
        & {t["symbol"] for t in result["short"]["qualification"]["truffles"]}
    )
    return result


async def _snapshot(
    repo: SnifferRepository,
    frame_time: datetime,
    candidates_dir: Path,
    qualifications_dir: Path | None,
    configured: dict[str, tuple[str, str | None]],
) -> dict[str, Any]:
    """Everything about one analyzed frame that does not depend on the
    clock: both rankings, their diagnostics, and qualification checks."""
    sniffer = await repo.get_for_frame(frame_time)
    assert sniffer is not None
    instruments = sorted(sniffer.instruments, key=lambda i: i.symbol)
    symbols = [i.symbol for i in instruments]
    names = {name for i in instruments for name in i.features}
    features = {name: [_float(i.features.get(name)) for i in instruments] for name in names}
    version = _contract_version(sniffer.analysis)
    snapshot: dict[str, Any] = {
        "frame": {
            "frame_time": frame_time,
            "analyzed_instruments": len(instruments),
            "analyzed_at": sniffer.analyzed_at,
            "analysis": sniffer.analysis,
            "temporal_contract_version": version,
        },
        "checks": {},
    }
    for direction, (candidate_id, qualification_id) in configured.items():
        ranking = rank_direction(candidate_id, direction, candidates_dir, symbols, features)
        ranking["diagnostics"] = await _diagnostics(
            repo, ranking, frame_time, version, candidates_dir, candidate_id
        )
        qualification, checks = _qualify(
            ranking, qualifications_dir, qualification_id, symbols, features
        )
        ranking["qualification"] = qualification
        snapshot["checks"][direction] = checks
        snapshot[direction] = ranking
    return snapshot


async def _diagnostics(
    repo: SnifferRepository,
    ranking: dict[str, Any],
    frame_time: datetime,
    version: int,
    candidates_dir: Path,
    candidate_id: str,
) -> dict[str, Any]:
    model = ranking.get("model") or {}
    base = {
        "frame_time": frame_time,
        "candidate_id": model.get("candidate_id"),
        "spec_hash": model.get("spec_hash"),
        "temporal_contract_version": version,
        "limitations": list(DIAGNOSTIC_LIMITATIONS),
    }
    if not ranking["available"]:
        return {**base, "available": False, "reason": ranking["reason"]}
    rows = ranking["rows"]
    scores = [r["score"] for r in rows]
    return {
        **base,
        "available": True,
        "eligible": len(rows),
        "distribution": diagnostics.score_distribution(scores),
        "separation": diagnostics.separation(scores),
        "stability": await _stability(
            repo, frame_time, version, candidates_dir, candidate_id, [r["symbol"] for r in rows]
        ),
    }


async def _stability(
    repo: SnifferRepository,
    frame_time: datetime,
    version: int,
    candidates_dir: Path,
    candidate_id: str,
    current: list[str],
) -> dict[str, Any]:
    """Against the frame exactly one boundary earlier, ranked by the same
    candidate spec from its own stored Sniffer features — never a partial
    or substitute frame, never a later frame."""
    previous_time = frame_time - BASE_DURATION
    base = {"previous_frame_time": previous_time, "k": diagnostics.TOP_K}
    candidate = load_live_candidate(candidates_dir, candidate_id)
    stored = await repo.get_metrics_for_frame(previous_time, candidate.features)
    if stored is None:
        return {
            **base,
            "available": False,
            "reason": "insufficient history: the previous frame has no Sniffer analysis",
        }
    analysis, by_symbol = stored
    if _contract_version(analysis) != version:
        return {
            **base,
            "available": False,
            "reason": "not comparable: the previous frame was measured under temporal "
            f"contract v{_contract_version(analysis)}, this one under v{version}",
        }
    symbols = sorted(by_symbol)
    previous_features = {
        f: [_float(by_symbol[s].get(f)) for s in symbols] for f in candidate.features
    }
    eligible = eligible_indices(previous_features, candidate.features)
    if len(eligible) < MIN_CROSS_SECTION:
        return {
            **base,
            "available": False,
            "reason": f"the previous frame had {len(eligible)} eligible instruments "
            f"(< {MIN_CROSS_SECTION})",
        }
    previous_symbols = [symbols[i] for i in eligible]
    scored = score_cross_section(
        candidate,
        previous_symbols,
        # Eligible positions hold finite values only.
        {f: [float(previous_features[f][i] or 0) for i in eligible] for f in candidate.features},
    )
    previous = [previous_symbols[i] for i in scored.order]
    return {
        **base,
        "spec_hash": candidate_hash(candidate),
        **diagnostics.stability(current, previous),
    }


def _qualify(
    ranking: dict[str, Any],
    qualifications_dir: Path | None,
    qualification_id: str | None,
    symbols: list[str],
    features: dict[str, list[Any]],
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    """Instrument checks over the ranked universe (frame gates are applied
    per request by `_gate`). Never changes a score or an order."""

    def unavailable(reason: str, spec: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"status": "unavailable", "reason": reason, "spec": spec, "truffles": []}

    if qualifications_dir is None or qualification_id is None:
        return unavailable("no qualification spec is configured"), {}
    try:
        spec = load_qualification(qualifications_dir, qualification_id)
    except ValueError as exc:
        return unavailable(str(exc)), {}
    summary = spec_summary(spec)
    model = ranking.get("model") or {}
    if spec.direction != ranking["direction"] or spec.ranking_candidate != model.get(
        "candidate_id"
    ):
        return unavailable(
            f"{spec.qualification_id} applies to {spec.ranking_candidate} ({spec.direction}), "
            f"not to this {ranking['direction']} ranking",
            summary,
        ), {}
    if not ranking["available"]:
        return unavailable(f"the ranking is unavailable: {ranking['reason']}", summary), {}
    rows = ranking["rows"]
    index = {s: i for i, s in enumerate(symbols)}
    ranked = [r["symbol"] for r in rows]
    checks = evaluate(
        spec,
        ranked,
        {
            f: [features[f][index[s]] if f in features else None for s in ranked]
            for f in spec.features
        },
    )
    truffles = []
    for row in rows:
        own = checks[row["symbol"]]
        row["qualification"] = {
            "qualified": qualified(own),
            "failed": [c["id"] for c in own if c["outcome"] == "failed"],
            "missing": [c["id"] for c in own if c["outcome"] == "missing"],
        }
        if row["qualification"]["qualified"]:
            # Compact: every condition passed, and the observed values are the
            # row's own contributions; full checks come with a symbol slice.
            truffles.append({"rank": row["rank"], "symbol": row["symbol"], "score": row["score"]})
    return {
        "status": "evaluated",
        "reason": None,
        "spec": summary,
        "evaluated": len(rows),
        "qualified": len(truffles),
        "truffles": truffles,
    }, checks


def _gate(ranking: dict[str, Any], stale_reason: str | None, completeness: float) -> dict[str, Any]:
    """Apply the spec's frame conditions, which depend on the clock: a
    stale or incomplete snapshot never shows Truffles (it is "withheld",
    which is not the same as zero qualified)."""
    qualification = ranking["qualification"]
    if qualification["status"] == "unavailable":
        return ranking
    minimum = qualification["spec"]["min_frame_completeness"]
    frame_checks: list[dict[str, Any]] = [
        {
            "id": "frame_fresh",
            "observed": stale_reason,
            "outcome": "failed" if stale_reason else "passed",
        },
        {
            "id": "frame_completeness",
            "observed": completeness,
            "threshold": {"at_least": minimum},
            "outcome": "passed" if completeness >= minimum else "failed",
        },
    ]
    if all(c["outcome"] == "passed" for c in frame_checks):
        return {**ranking, "qualification": {**qualification, "frame_checks": frame_checks}}
    reason = (
        f"stale ranking: {stale_reason}"
        if stale_reason
        else f"frame completeness {completeness:.3f} is below {minimum}"
    )
    return {
        **ranking,
        "rows": [{**r, "qualification": None} for r in ranking["rows"]],
        "qualification": {
            **qualification,
            "status": "withheld",
            "reason": reason,
            "frame_checks": frame_checks,
            "qualified": None,
            "truffles": [],
        },
    }


def _only(
    ranking: dict[str, Any], symbol: str | None, checks: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    """One coin's row/exclusion of a full ranking (audit F-08: a symbol page
    needs ~1KB, not the whole ~1.4MB board). Ranked over the whole frame
    first, so rank and `eligible` are exactly the full board's. That row
    also carries its full qualification checks (the board carries only
    which conditions failed)."""
    if symbol is None:
        return ranking
    rows = []
    for r in ranking.get("rows", []):
        if r["symbol"] != symbol:
            continue
        if r.get("qualification") is not None and symbol in checks:
            r = {**r, "qualification": {**r["qualification"], "checks": checks[symbol]}}
        rows.append(r)
    return {
        **ranking,
        "rows": rows,
        "excluded": [e for e in ranking.get("excluded", []) if e["symbol"] == symbol],
    }
