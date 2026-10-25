"""Published ranking snapshots (app.services.ranking_snapshots): capture at
publication, immutability, history API, and the pure comparison."""

import json
import shutil
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_truffle_qualification import (
    CANDIDATES,
    FRAME,
    LONG,
    LONG_Q,
    QUALIFICATIONS,
    RANKING,
    SHORT,
    SHORT_Q,
    _board,
    _seed,
)

from app.core.config import get_settings
from app.models.ranking_snapshot import RankingSnapshot
from app.services import live_rankings as live
from app.services import ranking_snapshots as rs
from app.services.sniffer import Sniffer


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


PUBLISHED = FRAME + timedelta(minutes=2)  # fresh, not stale


def _publisher(session_factory, candidates=CANDIDATES, qualifications=QUALIFICATIONS, at=PUBLISHED):
    return rs.RankingPublisher(
        session_factory,
        candidates_dir=candidates,
        qualifications_dir=qualifications,
        long_candidate=LONG,
        short_candidate=SHORT,
        long_qualification=LONG_Q,
        short_qualification=SHORT_Q,
        clock=lambda: at,
    )


async def _rows(session_factory) -> list[RankingSnapshot]:
    async with session_factory() as session:
        return list(
            (await session.execute(select(RankingSnapshot).order_by(RankingSnapshot.id))).scalars()
        )


# -- capture -----------------------------------------------------------------------------------


async def test_a_publication_is_captured_with_its_metadata_and_explanations(
    db_session, session_factory
):
    await _seed(db_session, _board())
    publisher = _publisher(session_factory)
    assert await publisher.publish(FRAME) == "captured"
    [row] = await _rows(session_factory)
    assert row.frame_time == FRAME and row.published_at == FRAME + timedelta(minutes=2)
    assert row.analyzed_at == FRAME + timedelta(seconds=70) and row.analysis == "live"
    assert (row.long_candidate, row.long_qualification) == (LONG, LONG_Q)
    assert len(row.long_candidate_hash) == len(row.short_qualification_hash) == 64
    assert (row.long_truffles, row.long_qualification_status) == (2, "evaluated")
    assert (row.short_truffles, row.temporal_contract_version) == (0, 2)
    payload = rs.decode_payload(row.payload)
    long = payload["long"]
    assert [t["symbol"] for t in long["qualification"]["truffles"]] == ["C001USDT", "C002USDT"]
    first = long["rows"][0]
    assert first["symbol"] == "C003USDT" and first["qualification"]["qualified"] is False
    assert {c["id"] for c in first["qualification"]["checks"]} == {
        "return_tail",
        "return_fell",
        "oi_tail",
        "oi_fell",
    }
    assert long["diagnostics"]["available"] and payload["frame"]["stale"] is False
    # Exactly what the live endpoint serves, scores at full precision.
    served = await live.live_rankings(
        db_session,
        candidates_dir=CANDIDATES,
        long_candidate=LONG,
        short_candidate=SHORT,
        now=FRAME + timedelta(minutes=2),
        qualifications_dir=QUALIFICATIONS,
        long_qualification=LONG_Q,
        short_qualification=SHORT_Q,
    )
    assert [r["score"] for r in long["rows"]] == [r["score"] for r in served["long"]["rows"]]
    assert publisher.status.captured == 1 and publisher.status.last_overhead_ms is not None


async def test_repeated_capture_is_idempotent(db_session, session_factory):
    await _seed(db_session, _board())
    assert await _publisher(session_factory).publish(FRAME) == "captured"
    # A later attempt at the same publication (another clock, another
    # process) is a duplicate, not a conflict.
    live._SNAPSHOTS.clear()
    later = _publisher(session_factory, at=PUBLISHED + timedelta(seconds=40))
    assert await later.publish(FRAME) == "duplicate"
    assert len(await _rows(session_factory)) == 1


async def test_a_conflicting_publication_is_reported_and_the_original_kept(
    db_session, session_factory
):
    await _seed(db_session, _board())
    await _publisher(session_factory).publish(FRAME)
    [original] = await _rows(session_factory)
    payload = rs.decode_payload(original.payload)
    payload["long"]["rows"][0]["score"] = 0.0  # a different result for the same publication
    row = rs.snapshot_row(payload, original.published_at, original.created_at)
    async with session_factory() as session:
        assert await rs._insert(session, row) == "conflict"
    [kept] = await _rows(session_factory)
    assert kept.payload_sha256 == original.payload_sha256


async def test_only_the_frame_served_live_is_captured(db_session, session_factory):
    await _seed(db_session, _board())
    publisher = _publisher(session_factory)
    assert await publisher.publish(FRAME - timedelta(minutes=5)) == "not_published"
    assert await _rows(session_factory) == []


async def test_a_storage_failure_is_counted_never_reported_as_captured(
    db_session, session_factory, monkeypatch
):
    await _seed(db_session, _board())
    publisher = _publisher(session_factory)

    async def broken(session, row):
        raise RuntimeError("disk full")

    monkeypatch.setattr(rs, "_insert", broken)
    with pytest.raises(RuntimeError):
        await publisher.publish(FRAME)
    assert publisher.status.failures == 1 and publisher.status.captured == 0
    assert "disk full" in publisher.status.last_error
    assert await _rows(session_factory) == []


async def test_sniffer_publishes_after_committing_and_survives_a_capture_failure(session_factory):
    calls: list[datetime] = []

    async def publish(frame_time):
        calls.append(frame_time)
        raise RuntimeError("capture broke")

    from test_sniffer_service import (
        FRAME_TIME,
        _add_instrument,
        _finalize_complete_frame,
        _seed_full_hour,
    )

    iid = await _add_instrument(session_factory, "BTCUSDT")
    await _seed_full_hour(session_factory, iid, FRAME_TIME - timedelta(hours=1))
    await _finalize_complete_frame(session_factory, iid, FRAME_TIME)
    await Sniffer(session_factory, publish=publish).analyze_frame(FRAME_TIME)
    assert calls == [FRAME_TIME]
    from app.repositories.sniffer_repository import SnifferRepository

    async with session_factory() as session:
        assert await SnifferRepository(session).get_for_frame(FRAME_TIME) is not None


async def test_a_spec_change_is_a_new_publication_and_never_rewrites_history(
    db_session, session_factory, tmp_path
):
    await _seed(db_session, _board())
    await _publisher(session_factory).publish(FRAME)
    [first] = await _rows(session_factory)
    changed = tmp_path / "ranking"
    shutil.copytree(RANKING, changed)
    raw = json.loads((changed / "qualifications" / f"{LONG_Q}.json").read_text())
    raw["conditions"][0]["at_most"] = 0.1
    (changed / "qualifications" / f"{LONG_Q}.json").write_text(json.dumps(raw))
    live._SNAPSHOTS.clear()
    assert await _publisher(session_factory, qualifications=changed / "qualifications").publish(
        FRAME
    ) == ("captured")
    rows = await _rows(session_factory)
    assert len(rows) == 2 and rows[0].payload == first.payload
    assert rows[1].long_qualification_hash != first.long_qualification_hash


async def test_unavailable_qualification_is_preserved_as_unavailable(
    db_session, session_factory, tmp_path
):
    await _seed(db_session, _board())
    await _publisher(session_factory, qualifications=tmp_path / "none").publish(FRAME)
    [row] = await _rows(session_factory)
    assert row.long_qualification_status == "unavailable" and row.long_truffles is None
    assert row.long_qualification is None


# -- history API ---------------------------------------------------------------------------------


async def _publish_frames(db_session, session_factory, count, *, gap_after=None):
    ids = None
    times = []
    t = FRAME
    for i in range(count):
        ids = await _seed(db_session, _board(), frame_time=t, ids=ids)
        live._SNAPSHOTS.clear()
        await _publisher(session_factory, at=t + timedelta(minutes=2)).publish(t)
        times.append(t)
        t += timedelta(minutes=5 if gap_after != i else 20)
    return times


async def _login(client, test_user):
    await client.post(
        "/api/auth/login", json={"email": test_user.email, "password": "correct-horse-battery"}
    )


async def test_history_requires_auth_and_is_empty_honestly(client, test_user):
    assert (await client.get("/api/sniffer/rankings/history")).status_code == 401
    await _login(client, test_user)
    assert (await client.get("/api/sniffer/rankings/history")).json() == {
        "items": [],
        "next_before": None,
    }
    assert (await client.get("/api/sniffer/rankings/history/1")).status_code == 404


async def test_history_pages_newest_first_without_recomputing(
    client, test_user, db_session, session_factory, monkeypatch
):
    times = await _publish_frames(db_session, session_factory, 3)
    await _login(client, test_user)

    async def no_recompute(*args, **kwargs):
        raise AssertionError("history must never recompute rankings")

    monkeypatch.setattr(rs, "live_rankings", no_recompute)
    page = (await client.get("/api/sniffer/rankings/history", params={"limit": 2})).json()

    def when(item):
        return datetime.fromisoformat(item["frame_time"].replace("Z", "+00:00"))

    assert [when(i) for i in page["items"]] == [times[2], times[1]]
    rest = (
        await client.get(
            "/api/sniffer/rankings/history", params={"limit": 2, "before": page["next_before"]}
        )
    ).json()
    assert [when(i) for i in rest["items"]] == [times[0]]
    assert rest["next_before"] is None
    assert "payload" not in page["items"][0]


async def test_detail_returns_the_stored_snapshot_neighbours_and_comparison(
    client, test_user, db_session, session_factory
):
    await _publish_frames(db_session, session_factory, 2)
    await _login(client, test_user)
    newest = (await client.get("/api/sniffer/rankings/history")).json()["items"][0]
    detail = (await client.get(f"/api/sniffer/rankings/history/{newest['id']}")).json()
    assert detail["snapshot"]["id"] == newest["id"] and detail["next"] is None
    assert detail["previous"]["id"] != newest["id"]
    comparison = detail["comparison"]
    assert comparison["consecutive"] and comparison["frame_gap_seconds"] == 300
    assert comparison["long"]["truffles"] == {
        "comparable": True,
        "count": 2,
        "previous_count": 2,
        "count_change": 0,
        "gained": [],
        "lost": [],
    }
    first = (await client.get(f"/api/sniffer/rankings/history/{detail['previous']['id']}")).json()
    assert first["comparison"]["available"] is False and first["previous"] is None
    one = (
        await client.get(
            f"/api/sniffer/rankings/history/{newest['id']}", params={"symbol": "c001usdt"}
        )
    ).json()
    assert [r["symbol"] for r in one["ranking"]["long"]["rows"]] == ["C001USDT"]
    assert one["ranking"]["long"]["rows"][0]["qualification"]["checks"]


# -- comparison (pure) -----------------------------------------------------------------------


def _ranking(symbols, truffles=(), status="evaluated", model="m1", policy="p1"):
    return {
        "available": True,
        "model": {"spec_hash": model},
        "rows": [{"symbol": s, "rank": i + 1} for i, s in enumerate(symbols)],
        "qualification": {
            "status": status,
            "spec": {"spec_hash": policy},
            "truffles": [{"symbol": s} for s in truffles] if status == "evaluated" else [],
        },
    }


BOARD = [f"S{i:02d}" for i in range(15)]


def test_movements_entries_and_exits():
    before = _ranking(BOARD)
    now = _ranking(["S12", *BOARD[:12], "S13", "S14"])  # S12 jumps in at #1
    out = rs.compare_direction(now, before)
    assert out["entered_top_k"] == ["S12"] and out["left_top_k"] == ["S09"]
    assert out["movements"]["S12"] == {"rank": 1, "previous_rank": 13, "change": 12}
    assert out["movements"]["S00"] == {"rank": 2, "previous_rank": 1, "change": -1}
    assert not out["model_changed"] and not out["policy_changed"]


def test_truffles_gained_lost_and_unchanged():
    out = rs.compare_direction(_ranking(BOARD, ["S01", "S02"]), _ranking(BOARD, ["S02", "S03"]))
    t = out["truffles"]
    assert (t["gained"], t["lost"], t["count_change"]) == (["S01"], ["S03"], 0)
    zero = rs.compare_direction(_ranking(BOARD, []), _ranking(BOARD, ["S02"]))["truffles"]
    assert (zero["count"], zero["lost"]) == (0, ["S02"])


def test_withheld_qualification_is_not_compared_as_zero():
    out = rs.compare_direction(_ranking(BOARD, status="withheld"), _ranking(BOARD, ["S02"]))
    assert out["truffles"] == {
        "comparable": False,
        "status": "withheld",
        "previous_status": "evaluated",
    }


def test_spec_changes_are_flagged_as_not_comparable():
    out = rs.compare_direction(
        _ranking(BOARD, ["S01"], model="m2", policy="p2"), _ranking(BOARD, ["S01"])
    )
    assert out["model_changed"] and out["policy_changed"] and out["truffles"]["comparable"] is False


def test_gaps_are_reported_and_only_five_minutes_is_consecutive():
    a = {"id": 1, "frame_time": FRAME.isoformat()}
    b = {"id": 2, "frame_time": (FRAME + timedelta(minutes=20)).isoformat()}
    out = rs.compare_snapshots(
        b,
        {"long": _ranking(BOARD), "short": _ranking(BOARD)},
        a,
        {"long": _ranking(BOARD), "short": _ranking(BOARD)},
    )
    assert out["frame_gap_seconds"] == 1200 and out["consecutive"] is False
    unavailable = rs.compare_direction({"available": False}, _ranking(BOARD))
    assert unavailable["available"] is False


def test_the_configured_specs_are_those_published():
    settings = get_settings()
    assert (settings.live_ranking_long_candidate, settings.live_truffle_long_qualification) == (
        LONG,
        LONG_Q,
    )
