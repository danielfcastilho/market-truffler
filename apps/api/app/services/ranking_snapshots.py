"""Published ranking snapshots: the Long/Short rankings exactly as Sniffer
made them available, kept immutably for historical browsing.

PUBLICATION. A frame's ranking becomes available through
`GET /api/sniffer/rankings` the moment Sniffer commits that frame's
analysis *and* it is the newest analyzed frame (the endpoint serves only
the newest). That is the boundary: `Sniffer.analyze_frame` calls
`RankingPublisher.publish` right after committing, the publication time is
taken then, and the response is produced by the same `live_rankings` call
the endpoint makes (which also fills its cache — nothing is computed
twice). A frame that is not the newest when analyzed (an older frame
recovered after a restart) was never served live and is not captured.
Publication means the system made the result available — not that anyone
viewed it.

Nothing is reconstructed: no snapshot exists for a frame published before
this capture existed, and none is ever backfilled. A snapshot is not a
replay and not a backtest; it records what was published.

IMMUTABILITY. One row per publication, identified by the frame, the instant
its analysis was stored and the four spec hashes (`uq_ranking_snapshot_
publication`). Rows are inserted, never updated: a repeated capture of the
same publication is a no-op; one whose payload differs from the stored one
is reported as a conflict and the original kept. Each row is one INSERT, so
a partial write is never readable.
"""

import gzip
import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ranking_snapshot import RankingSnapshot
from app.repositories.sniffer_repository import SnifferRepository
from app.services.live_rankings import live_rankings

logger = logging.getLogger(__name__)

#: 2: `payload_sha256` covers the published content only (see
#: `_CAPTURE_TIME_FIELDS`). Version-1 rows (the first four captures,
#: 2026-10-25 14:05-14:20 UTC) hash the whole payload; they are kept as
#: written, never rehashed.
SNAPSHOT_SCHEMA_VERSION = 2
TOP_K = 10
MAX_PAGE = 100


def _json_default(value: Any) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


#: Frame fields that describe the instant of capture, not the published
#: ranking: stored as captured, but excluded from the content digest so a
#: repeated capture of the same publication is recognised as a duplicate.
_CAPTURE_TIME_FIELDS = ("age_seconds", "stale", "stale_reason")


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=_json_default
    ).encode()


def encode_payload(payload: dict[str, Any]) -> tuple[bytes, str]:
    """(gzip-compressed canonical JSON of the whole payload, sha256 of its
    published content — the payload without `_CAPTURE_TIME_FIELDS`). Floats
    keep full precision (`repr`)."""
    frame = {k: v for k, v in (payload.get("frame") or {}).items() if k not in _CAPTURE_TIME_FIELDS}
    content = _canonical({**payload, "frame": frame})
    return gzip.compress(_canonical(payload), mtime=0), hashlib.sha256(content).hexdigest()


def decode_payload(blob: bytes) -> dict[str, Any]:
    value = json.loads(gzip.decompress(blob))
    assert isinstance(value, dict)
    return value


def _spec(
    ranking: dict[str, Any] | None,
) -> tuple[str | None, str | None, str | None, str | None, str | None, int | None]:
    """(candidate, candidate hash, qualification, qualification hash,
    qualification status, Truffle count) of one direction."""
    ranking = ranking or {}
    model = ranking.get("model") or {}
    qualification = ranking.get("qualification") or {}
    spec = qualification.get("spec") or {}
    status = qualification.get("status")
    return (
        model.get("candidate_id"),
        model.get("spec_hash"),
        spec.get("qualification_id"),
        spec.get("spec_hash"),
        status,
        len(qualification.get("truffles") or []) if status == "evaluated" else None,
    )


def snapshot_row(
    payload: dict[str, Any], published_at: datetime, created_at: datetime
) -> dict[str, Any]:
    frame = payload["frame"]
    long, short = _spec(payload.get("long")), _spec(payload.get("short"))
    spec_key = hashlib.sha256(
        "|".join(str(v) for v in (long[1], long[3], short[1], short[3])).encode()
    ).hexdigest()
    blob, digest = encode_payload(payload)
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "frame_time": _dt(frame["frame_time"]),
        "analyzed_at": _dt(frame["analyzed_at"]),
        "analysis": frame.get("analysis"),
        "published_at": published_at,
        "created_at": created_at,
        "temporal_contract_version": frame.get("temporal_contract_version"),
        "spec_key": spec_key,
        "long_candidate": long[0],
        "long_candidate_hash": long[1],
        "long_qualification": long[2],
        "long_qualification_hash": long[3],
        "long_qualification_status": long[4],
        "long_truffles": long[5],
        "short_candidate": short[0],
        "short_candidate_hash": short[1],
        "short_qualification": short[2],
        "short_qualification_hash": short[3],
        "short_qualification_status": short[4],
        "short_truffles": short[5],
        "payload_sha256": digest,
        "payload": blob,
    }


def _dt(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


@dataclass
class PublisherStatus:
    """In-process capture status (since this process started)."""

    captured: int = 0
    duplicates: int = 0
    conflicts: int = 0
    failures: int = 0
    last_error: str | None = None
    last_captured_frame: datetime | None = None
    last_published_at: datetime | None = None
    last_overhead_ms: float | None = None
    skipped: list[str] = field(default_factory=list)


class RankingPublisher:
    def __init__(
        self,
        session_factory: Any,
        *,
        candidates_dir: Path,
        qualifications_dir: Path,
        long_candidate: str,
        short_candidate: str,
        long_qualification: str,
        short_qualification: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._kw: dict[str, Any] = {
            "candidates_dir": candidates_dir,
            "qualifications_dir": qualifications_dir,
            "long_candidate": long_candidate,
            "short_candidate": short_candidate,
            "long_qualification": long_qualification,
            "short_qualification": short_qualification,
        }
        self.status = PublisherStatus()

    async def publish(self, frame_time: datetime) -> str:
        """Capture the publication of `frame_time`'s ranking, if it is the
        one now served live. Returns "captured", "duplicate", "conflict" or
        "not_published" (with the reason logged). Raises on a storage
        failure — the caller logs it; nothing is reported as captured."""
        perf = time.perf_counter()
        started = self._clock()
        published_at = started
        try:
            async with self._session_factory() as session:
                marker = await SnifferRepository(session).latest_marker()
                if marker is None or marker[0] != frame_time:
                    self.status.skipped = [
                        f"{frame_time.isoformat()}: not the newest analyzed frame"
                    ]
                    return "not_published"
                payload = await live_rankings(
                    session, now=published_at, include_checks=True, **self._kw
                )
                if not payload.get("available"):
                    self.status.skipped = [f"{frame_time.isoformat()}: {payload.get('reason')}"]
                    return "not_published"
                # `live_rankings` reads the newest frame again: if a newer one
                # was analyzed in between, this payload is that frame's
                # publication (captured by its own publish), not this one's.
                if _dt(payload["frame"]["frame_time"]) != frame_time:
                    self.status.skipped = [
                        f"{frame_time.isoformat()}: superseded before capture"
                    ]
                    return "not_published"
                outcome = await _insert(session, snapshot_row(payload, published_at, self._clock()))
        except Exception as exc:
            self.status.failures += 1
            self.status.last_error = f"{type(exc).__name__}: {exc}"[-500:]
            raise
        if outcome == "captured":
            self.status.captured += 1
            self.status.last_captured_frame = frame_time
            self.status.last_published_at = published_at
        elif outcome == "duplicate":
            self.status.duplicates += 1
        else:
            self.status.conflicts += 1
            logger.error("ranking_snapshot_conflict", extra={"frame_time": frame_time.isoformat()})
        # Wall time of the capture (the clock may be fixed in tests).
        self.status.last_overhead_ms = (time.perf_counter() - perf) * 1000
        return outcome


async def _insert(session: AsyncSession, row: dict[str, Any]) -> str:
    """INSERT … ON CONFLICT DO NOTHING; a stored publication is never
    replaced. Returns captured / duplicate / conflict."""
    dialect = session.bind.dialect.name if session.bind else "postgresql"
    insert_fn = pg_insert if dialect == "postgresql" else sqlite_insert
    stmt = (
        insert_fn(RankingSnapshot)
        .values(row)
        .on_conflict_do_nothing(index_elements=["frame_time", "analyzed_at", "spec_key"])
        # RETURNING, not rowcount: the driver's rowcount for a skipped
        # INSERT … ON CONFLICT is not reliable (psycopg reported a no-op as
        # an insert); a returned id is unambiguous on every dialect.
        .returning(RankingSnapshot.id)
    )
    inserted = (await session.execute(stmt)).scalar_one_or_none()
    await session.commit()
    if inserted is not None:
        return "captured"
    existing = (
        await session.execute(
            select(RankingSnapshot.payload_sha256).where(
                RankingSnapshot.frame_time == row["frame_time"],
                RankingSnapshot.analyzed_at == row["analyzed_at"],
                RankingSnapshot.spec_key == row["spec_key"],
            )
        )
    ).scalar_one()
    return "duplicate" if existing == row["payload_sha256"] else "conflict"


# -- reading -------------------------------------------------------------------------------


_SUMMARY = (
    "id",
    "frame_time",
    "analyzed_at",
    "analysis",
    "published_at",
    "temporal_contract_version",
    "long_candidate",
    "long_candidate_hash",
    "short_candidate",
    "short_candidate_hash",
    "long_qualification",
    "long_qualification_hash",
    "short_qualification",
    "short_qualification_hash",
    "long_qualification_status",
    "short_qualification_status",
    "long_truffles",
    "short_truffles",
    "schema_version",
    "payload_sha256",
)


def summary(row: RankingSnapshot) -> dict[str, Any]:
    return {name: getattr(row, name) for name in _SUMMARY}


def _before(published_at: datetime, snapshot_id: int, *, older: bool) -> ColumnElement[bool]:
    """Keyset condition on (published_at, id): strictly older or newer."""
    if older:
        return or_(
            RankingSnapshot.published_at < published_at,
            and_(RankingSnapshot.published_at == published_at, RankingSnapshot.id < snapshot_id),
        )
    return or_(
        RankingSnapshot.published_at > published_at,
        and_(RankingSnapshot.published_at == published_at, RankingSnapshot.id > snapshot_id),
    )


async def history_page(
    session: AsyncSession, *, limit: int, before: int | None = None
) -> dict[str, Any]:
    """Newest first by (published_at, id); `before` is the id of the last
    item of the previous page. Never loads payloads."""
    limit = max(1, min(limit, MAX_PAGE))
    stmt = select(RankingSnapshot).order_by(
        RankingSnapshot.published_at.desc(), RankingSnapshot.id.desc()
    )
    if before is not None:
        anchor = await session.get(RankingSnapshot, before)
        if anchor is None:
            return {"items": [], "next_before": None}
        stmt = stmt.where(_before(anchor.published_at, anchor.id, older=True))
    rows = list((await session.execute(stmt.limit(limit + 1))).scalars())
    items = [summary(r) for r in rows[:limit]]
    return {"items": items, "next_before": items[-1]["id"] if len(rows) > limit else None}


async def _neighbour(
    session: AsyncSession, row: RankingSnapshot, older: bool
) -> RankingSnapshot | None:
    stmt = select(RankingSnapshot).where(_before(row.published_at, row.id, older=older))
    order = (
        (RankingSnapshot.published_at.desc(), RankingSnapshot.id.desc())
        if older
        else (RankingSnapshot.published_at.asc(), RankingSnapshot.id.asc())
    )
    return (await session.execute(stmt.order_by(*order).limit(1))).scalar_one_or_none()


async def snapshot_detail(
    session: AsyncSession, snapshot_id: int, *, symbol: str | None = None
) -> dict[str, Any] | None:
    """One published snapshot as stored, its neighbours, and the comparison
    with the previous available snapshot — all from persisted payloads."""
    row = await session.get(RankingSnapshot, snapshot_id)
    if row is None:
        return None
    payload = decode_payload(row.payload)
    previous = await _neighbour(session, row, older=True)
    following = await _neighbour(session, row, older=False)
    comparison = (
        compare_snapshots(
            summary(row), payload, summary(previous), decode_payload(previous.payload)
        )
        if previous is not None
        else {"available": False, "reason": "no earlier published snapshot exists"}
    )
    if symbol is not None:
        for direction in ("long", "short"):
            ranking = payload.get(direction) or {}
            ranking["rows"] = [r for r in ranking.get("rows", []) if r.get("symbol") == symbol]
            ranking["excluded"] = [
                e for e in ranking.get("excluded", []) if e.get("symbol") == symbol
            ]
    return {
        "snapshot": summary(row),
        "ranking": payload,
        "previous": summary(previous) if previous else None,
        "next": summary(following) if following else None,
        "comparison": comparison,
    }


# -- comparison (pure) ---------------------------------------------------------------------


def _truffles(ranking: dict[str, Any]) -> set[str] | None:
    qualification = ranking.get("qualification") or {}
    if qualification.get("status") != "evaluated":
        return None
    return {t["symbol"] for t in qualification.get("truffles") or []}


def compare_direction(
    current: dict[str, Any] | None, previous: dict[str, Any] | None, k: int = TOP_K
) -> dict[str, Any]:
    current, previous = current or {}, previous or {}
    if not current.get("available") or not previous.get("available"):
        return {"available": False, "reason": "a ranking in one of the snapshots was unavailable"}
    now_rank = {r["symbol"]: r["rank"] for r in current.get("rows", [])}
    before_rank = {r["symbol"]: r["rank"] for r in previous.get("rows", [])}
    top_now = [r["symbol"] for r in current["rows"][:k]]
    top_before = [r["symbol"] for r in previous["rows"][:k]]
    q_now = current.get("qualification") or {}
    q_before = previous.get("qualification") or {}
    spec_now = (q_now.get("spec") or {}).get("spec_hash")
    spec_before = (q_before.get("spec") or {}).get("spec_hash")
    model_changed = (current.get("model") or {}).get("spec_hash") != (
        previous.get("model") or {}
    ).get("spec_hash")
    policy_changed = spec_now != spec_before
    t_now, t_before = _truffles(current), _truffles(previous)
    truffles: dict[str, Any]
    if t_now is None or t_before is None:
        truffles = {
            "comparable": False,
            "status": q_now.get("status"),
            "previous_status": q_before.get("status"),
        }
    else:
        truffles = {
            "comparable": not policy_changed,
            "count": len(t_now),
            "previous_count": len(t_before),
            "count_change": len(t_now) - len(t_before),
            "gained": sorted(t_now - t_before),
            "lost": sorted(t_before - t_now),
        }
    return {
        "available": True,
        "k": k,
        "model_changed": model_changed,
        "policy_changed": policy_changed,
        "entered_top_k": [s for s in top_now if s not in set(top_before)],
        "left_top_k": [s for s in top_before if s not in set(top_now)],
        # Positive change = moved up the board (rank number decreased).
        "movements": {
            s: {
                "rank": now_rank[s],
                "previous_rank": before_rank.get(s),
                "change": before_rank[s] - now_rank[s] if s in before_rank else None,
            }
            for s in now_rank
            if s in top_now or (t_now is not None and s in t_now)
        },
        "truffles": truffles,
    }


def compare_snapshots(
    current_meta: dict[str, Any],
    current: dict[str, Any],
    previous_meta: dict[str, Any],
    previous: dict[str, Any],
) -> dict[str, Any]:
    """Selected snapshot versus the previous *available* published one —
    which may be more than one frame earlier (the gap is reported, and only
    a 5-minute gap is "consecutive")."""
    gap = (_dt(current_meta["frame_time"]) - _dt(previous_meta["frame_time"])).total_seconds()
    out: dict[str, Any] = {
        "available": True,
        "previous_id": previous_meta["id"],
        "previous_frame_time": previous_meta["frame_time"],
        "frame_gap_seconds": gap,
        "consecutive": gap == 300,
        "same_frame": gap == 0,
    }
    for direction in ("long", "short"):
        out[direction] = compare_direction(current.get(direction), previous.get(direction))
    return out
