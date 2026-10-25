"""Research data health: frame coverage on the 5-minute grid, shard
inventory from manifests only, the worker's continuity and readiness
reports, and read-only behaviour."""

import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_truffle_evaluation import _register as register_truffles
from test_truffle_evaluation import _shard as write_truffle_shard

from app.core.config import get_settings
from app.models.frame import MarketFrame as MarketFrameRow
from app.research.readiness import registration_readiness
from app.services import research_data_health as health

REPO = Path(__file__).resolve().parents[3]
RESEARCH = REPO / "research/oink_corp"
DAY = datetime(2026, 10, 1, tzinfo=UTC)
FIVE = timedelta(minutes=5)


def _rows(start, count, *, status="complete", expected=10, available=10, analysis="v2"):
    return [(start + i * FIVE, status, expected, available, analysis) for i in range(count)]


# -- frame coverage ---------------------------------------------------------------------------


def test_no_history_is_unavailable_not_zero():
    out = health.frame_coverage([], DAY, None)
    assert out["available"] is False and "no completed frame" in out["reason"]


def test_a_single_frame_is_a_one_slot_partial_day():
    out = health.frame_coverage(_rows(DAY, 1), DAY + timedelta(minutes=3), DAY)
    assert (out["expected"], out["finalized"], out["coverage"]) == (1, 1, 1.0)
    assert out["daily"][0]["partial_day"] == "first_and_last"


def test_a_complete_utc_day_has_288_frames_and_no_partial_flag():
    now = DAY + timedelta(days=1, minutes=1)  # last due boundary: 23:55
    out = health.frame_coverage(_rows(DAY, 288), now, DAY)
    [day] = out["daily"]
    assert (day["expected"], day["finalized"], day["partial_day"]) == (288, 288, None)
    assert day["coverage"] == 1.0 and day["largest_gap_minutes"] == 0
    assert out["missing_intervals"]["count"] == 0
    assert out["covered_intervals"]["items"] == [
        [DAY.isoformat(), (DAY + timedelta(days=1)).isoformat(), 288]
    ]


def test_partial_first_and_last_days_are_boundaries_not_failures():
    start = DAY + timedelta(hours=20)
    rows = _rows(start, 4 * 12 + 24)  # 20:00 -> next day 02:00
    out = health.frame_coverage(rows, start + 71 * FIVE + timedelta(minutes=2), start)
    first, last = out["daily"]
    assert (first["partial_day"], first["expected"], first["coverage"]) == ("first", 48, 1.0)
    assert (last["partial_day"], last["expected"], last["coverage"]) == ("last", 24, 1.0)


def test_missing_frames_gaps_and_intervals():
    rows = _rows(DAY, 10) + _rows(DAY + 15 * FIVE, 5)  # slots 10-14 missing
    out = health.frame_coverage(rows, DAY + 19 * FIVE + timedelta(minutes=2), DAY)
    assert (out["expected"], out["missing"], out["finalized"]) == (20, 5, 15)
    assert out["daily"][0]["largest_gap_minutes"] == 25
    assert out["missing_intervals"]["items"] == [
        [(DAY + 10 * FIVE).isoformat(), (DAY + 15 * FIVE).isoformat(), 5]
    ]
    assert out["coverage"] == pytest.approx(0.75)


def test_incomplete_partial_and_unanalyzed_frames_are_distinguished():
    rows = [
        (DAY, "complete", 10, 10, "v2"),
        (DAY + FIVE, "partial", 10, 7, "v2"),
        (DAY + 2 * FIVE, "building", 10, 0, None),  # interrupted, never finalized
        (DAY + 3 * FIVE, "complete", 10, 10, None),  # finalized, no analysis
        (DAY + 4 * FIVE, "complete", 10, 10, "v1"),
    ]
    out = health.frame_coverage(rows, DAY + 4 * FIVE + timedelta(minutes=2), DAY)
    assert (out["complete"], out["partial"], out["building"], out["missing"]) == (3, 1, 1, 0)
    assert out["finalized"] == 4 and out["coverage"] == pytest.approx(0.8)
    assert (out["analyzed"], out["unanalyzed"]) == (3, 1)
    assert out["analyzed_by_contract"]["v1"]["frames"] == 1
    assert out["analyzed_by_contract"]["v2"]["frames"] == 2


def test_results_older_than_retention_are_unknown_not_unanalyzed():
    rows = _rows(DAY, 4, analysis=None)
    out = health.frame_coverage(rows, DAY + 3 * FIVE + timedelta(minutes=2), DAY + 2 * FIVE)
    assert (out["analysis_unknown"], out["unanalyzed"]) == (2, 2)


def test_the_forming_frame_and_off_grid_frames_never_count():
    rows = _rows(DAY, 3) + [
        (DAY + 3 * FIVE, "building", 10, 0, None),  # forming now
        (DAY + timedelta(minutes=7), "complete", 10, 10, "v1"),  # legacy 1-minute era
    ]
    out = health.frame_coverage(rows + rows[:1], DAY + 3 * FIVE + timedelta(seconds=40), DAY)
    assert out["expected"] == 3 and out["building"] == 0 and out["missing"] == 0
    assert out["off_grid_frames"] == 1 and out["duplicate_rows"] == 1
    assert out["latest_frame"] == (DAY + 2 * FIVE).isoformat()


def test_utc_day_boundaries_split_at_midnight():
    start = DAY + timedelta(hours=23, minutes=55)
    out = health.frame_coverage(_rows(start, 2), start + FIVE + timedelta(minutes=2), start)
    assert [d["day"] for d in out["daily"]] == ["2026-10-01", "2026-10-02"]
    assert [d["expected"] for d in out["daily"]] == [1, 1]


def test_coverage_is_deterministic():
    rows = _rows(DAY, 50) + _rows(DAY + 60 * FIVE, 10)
    now = DAY + 80 * FIVE
    assert health.frame_coverage(rows, now, DAY) == health.frame_coverage(rows[::-1], now, DAY)


# -- shard inventory ---------------------------------------------------------------------------


def _dataset(data: Path, name: str, **manifest):
    path = data / name
    path.mkdir(parents=True)
    (path / "data.parquet").write_bytes(b"not read")
    base = {
        "data_sha256": "a" * 64,
        "first_frame_time": "2026-10-12T00:00:00+00:00",
        "last_frame_time": "2026-10-12T23:45:00+00:00",
        "row_count": 10,
        "sample_interval_minutes": 15,
        "horizons_minutes": {"1h": 60},
    }
    (path / "manifest.json").write_text(json.dumps({**base, **manifest}))
    return path


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            digest.update(str(p.relative_to(root)).encode() + p.read_bytes())
    return digest.hexdigest()


def test_the_inventory_reads_manifests_only_and_reports_what_is_absent(tmp_path):
    _dataset(
        tmp_path,
        "shard_s15m_20261012",
        shard_day="2026-10-12",
        temporal_contract_version=2,
        generated_at="2026-10-13T05:00:00+00:00",
    )
    _dataset(
        tmp_path,
        "shard_s15m_20261013",
        shard_day="2026-10-13",
        first_frame_time="2026-10-13T00:00:00+00:00",
        last_frame_time="2026-10-13T23:45:00+00:00",
    )
    nomanifest = tmp_path / "shard_s15m_20261014"
    nomanifest.mkdir()
    nofile = _dataset(
        tmp_path,
        "shard_s15m_20261015",
        shard_day="2026-10-14",
        first_frame_time="2026-10-15T00:00:00+00:00",
        last_frame_time="2026-10-15T23:45:00+00:00",
    )
    (nofile / "data.parquet").unlink()
    before = _tree_hash(tmp_path)

    worker = {"datasets": [{"name": "shard_s15m_20261012", "verified_now": True}]}
    out = health.shard_inventory(tmp_path, worker)
    by_name = {d["name"]: d for d in out["datasets"]}

    a = by_name["shard_s15m_20261012"]
    assert (a["verification"], a["temporal_contract_version"], a["manifest_problems"]) == (
        "verified",
        2,
        [],
    )
    b = by_name["shard_s15m_20261013"]
    assert b["verification"] == "not_checked"  # parsing a manifest is not verifying it
    assert b["temporal_contract_version"] is None and b["generated_at"] is None
    assert by_name["shard_s15m_20261014"]["manifest"] == "missing"
    c = by_name["shard_s15m_20261015"]
    assert c["data_file_present"] is False
    assert any("differs from its name" in p for p in c["manifest_problems"])
    assert out["overlaps"] == []
    assert _tree_hash(tmp_path) == before  # read-only


def test_overlapping_and_duplicate_intervals_are_detected(tmp_path):
    _dataset(tmp_path, "research_a")
    _dataset(tmp_path, "research_b")  # identical interval
    _dataset(
        tmp_path,
        "research_c",
        first_frame_time="2026-10-12T12:00:00+00:00",
        last_frame_time="2026-10-13T12:00:00+00:00",
    )
    kinds = {
        tuple(o["datasets"]): o["kind"] for o in health.shard_inventory(tmp_path, None)["overlaps"]
    }
    assert kinds[("research_a", "research_b")] == "duplicate"
    assert kinds[("research_a", "research_c")] == "overlap"


def test_mixed_contract_versions_are_reported_separately(tmp_path):
    _dataset(tmp_path, "shard_s15m_20261012", temporal_contract_version=2)
    _dataset(
        tmp_path,
        "legacy",
        first_frame_time="2026-09-01T00:00:00+00:00",
        last_frame_time="2026-09-02T00:00:00+00:00",
    )
    summary = health.contract_summary({"available": False}, health.shard_inventory(tmp_path, None))
    assert summary["datasets"] == {
        "unrecorded": {"count": 1, "names": ["legacy"]},
        "v2": {"count": 1, "names": ["shard_s15m_20261012"]},
    }


def test_shard_continuity_is_the_workers_own_day_decisions():
    worker = {
        "written_at": "2026-10-25T01:00:00+00:00",
        "days": [
            {"day": "2026-10-12", "status": "preserved"},
            {"day": "2026-10-14", "status": "collection_gap"},
            {"day": "2026-10-24", "status": "waiting_maturity"},
        ],
    }
    out = health.shard_continuity(worker)
    assert out["by_status"]["collection_gap"] == {"count": 1, "days": ["2026-10-14"]}
    assert health.shard_continuity(None)["available"] is False


# -- readiness ------------------------------------------------------------------------------


@pytest.fixture
def lab(tmp_path):
    ranking = tmp_path / "ranking"
    shutil.copytree(RESEARCH / "ranking", ranking)
    data = tmp_path / "data"
    (data / "evaluations").mkdir(parents=True)
    shutil.copytree(
        RESEARCH / "data/evaluations/oi-price-1h-prospective-v1",
        data / "evaluations/oi-price-1h-prospective-v1",
    )
    return {"data": data, "ranking": ranking}


def _ready(lab, now):
    return {
        r["registration_id"]: r
        for r in registration_readiness(
            lab["data"],
            campaigns_dir=RESEARCH / "campaigns",
            experiments_dir=RESEARCH / "experiments",
            ranking_dir=lab["ranking"],
            now=now,
        )
    }


def test_both_registration_kinds_report_authoritative_phases(lab):
    from test_truffle_evaluation import HOLDOUT, VALIDATION

    register_truffles(lab)  # synthetic truffle windows in September/October
    out = _ready(lab, datetime(2026, 9, 25, 12, tzinfo=UTC))
    existing = out["oi-price-1h-prospective-v1"]
    assert (existing["kind"], existing["verification"]) == ("campaign_claims", "verified")
    assert [p["phase"] for p in existing["partitions"]] == ["awaiting_window", "awaiting_window"]
    truffle = out["truffle-test-v1"]
    assert truffle["kind"] == "truffle_qualification"
    validation, holdout = truffle["partitions"]
    assert validation["phase"] == "accumulating" and validation["days_elapsed"] == 3
    assert validation["missing_past_days"] == ["2026-09-22", "2026-09-23", "2026-09-24"]

    for d in range(10):
        write_truffle_shard(lab["data"], VALIDATION.start + timedelta(days=d))
    ended = _ready(lab, datetime(2026, 10, 2, 0, 0, 1, tzinfo=UTC))["truffle-test-v1"]
    assert ended["partitions"][0]["phase"] == "eligible" and ended["partitions"][0]["eligible_now"]
    late = _ready(lab, datetime(2026, 10, 13, 6, tzinfo=UTC))["truffle-test-v1"]["partitions"][1]
    # Ended with no shards: the protocol's readiness fallback has not come yet.
    assert late["phase"] == "awaiting_eligibility" and late["missing_past_days"]
    holdout_ready = _ready(lab, datetime(2026, 10, 16, tzinfo=UTC))["truffle-test-v1"][
        "partitions"
    ][1]
    assert holdout_ready["phase"] == "eligible" and not holdout_ready["eligible_now"]
    assert "--protected-holdout" in holdout_ready["blocking"][0]
    assert len(holdout_ready["missing_past_days"]) == (HOLDOUT.end - HOLDOUT.start).days


def test_an_invalid_registration_is_reported_never_repaired(lab):
    register_truffles(lab)
    path = lab["data"] / "evaluations/truffle-test-v1/registration/registration.json"
    stored = json.loads(path.read_text())
    stored["registration"]["question"] = "edited after registration"
    path.write_text(json.dumps(stored))
    before = path.read_bytes()
    out = _ready(lab, datetime(2026, 10, 20, tzinfo=UTC))["truffle-test-v1"]
    assert out["verification"] == "invalid"
    assert any("was modified" in p for p in out["problems"])
    assert all("does not verify" in p["blocking"][0] for p in out["partitions"])
    assert path.read_bytes() == before


def test_readiness_writes_no_event_and_touches_nothing(lab):
    register_truffles(lab)
    before = _tree_hash(lab["data"])
    _ready(lab, datetime(2026, 11, 1, tzinfo=UTC))
    assert _tree_hash(lab["data"]) == before
    assert not any((lab["data"] / "evaluations").rglob("event.json"))


# -- endpoint ---------------------------------------------------------------------------------


async def test_the_endpoint_is_authenticated_read_only_and_never_opens_parquet(
    client, test_user, db_session, tmp_path, monkeypatch
):
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    start = now - timedelta(minutes=now.minute % 5) - timedelta(hours=1)
    for i in range(6):
        db_session.add(
            MarketFrameRow(
                frame_time=start + i * FIVE,
                expected_instrument_ids=[],
                expected_instruments=0,
                available_instruments=0,
                status="complete",
                created_at=start,
                finalized_at=start,
            )
        )
    await db_session.commit()
    _dataset(tmp_path, "shard_s15m_20261012", temporal_contract_version=2)
    (tmp_path / "continuity").mkdir()
    (tmp_path / "continuity/status.json").write_text(
        json.dumps({"written_at": now.isoformat(), "days": [], "registrations": []})
    )
    monkeypatch.setattr(get_settings(), "research_data_dir", str(tmp_path))
    health._cache.clear()

    import pyarrow.parquet as pq

    def refuse(*args, **kwargs):
        raise AssertionError("data health must not open a Parquet file")

    monkeypatch.setattr(pq, "read_table", refuse)
    monkeypatch.setattr(pq, "ParquetFile", refuse)
    assert (await client.get("/api/research/data-health")).status_code == 401
    await client.post(
        "/api/auth/login", json={"email": test_user.email, "password": "correct-horse-battery"}
    )
    before = _tree_hash(tmp_path)
    body = (await client.get("/api/research/data-health")).json()
    assert body["frames"]["available"] and body["frames"]["expected"] >= 6
    assert body["frames"]["missing"] == body["frames"]["expected"] - 6
    assert body["shards"]["datasets"][0]["verification"] == "not_checked"
    assert body["readiness"]["available"] and body["readiness"]["registrations"] == []
    assert body["warnings"] and body["limitations"]
    again = (await client.get("/api/research/data-health")).json()
    assert again["computed_at"] == body["computed_at"]  # cached, and says when
    assert _tree_hash(tmp_path) == before
    health._cache.clear()
