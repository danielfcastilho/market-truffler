"""Scheduled shard verification (app.research.integrity), prospective
continuity alerts (app.research.research_alerts) and recent Frame
coverage (app.services.research_data_health.recent_continuity)."""

import json
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from research_helpers import write_dataset

from app.research import integrity
from app.research.research_alerts import derive_alerts, update_alerts
from app.routers.research import research_status_from_file
from app.services.research_data_health import recent_continuity

REPO = Path(__file__).resolve().parents[3]
EXISTING = REPO / "research/oink_corp/data/evaluations/oi-price-1h-prospective-v1"
NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)
DAY = timedelta(hours=24)
FIVE = timedelta(minutes=5)


def _shard(data: Path, name: str, day: date) -> Path:
    t = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    rows = [
        {
            "frame_time": t + timedelta(minutes=15 * i),
            "symbol": "S",
            "forward_relative_return_1h": 0.1,
        }
        for i in range(4)
    ]
    write_dataset(data / name, rows, sample_interval_minutes=15)
    path = data / name / "manifest.json"
    stored = json.loads(path.read_text())
    stored["row_count"] = 4
    path.write_text(json.dumps(stored))
    return data / name


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def _run(data, now=NOW, **kw):
    return integrity.run_verification(
        data,
        data / "continuity",
        now=now,
        interval=kw.pop("interval", DAY),
        max_per_pass=kw.pop("max_per_pass", 20),
        **kw,
    )


def _status(data, now=NOW):
    return integrity.verification_report(data, data / "continuity", now, DAY)["datasets"]


# -- scheduled verification -----------------------------------------------------------------


def test_verification_runs_when_due_and_not_before(tmp_path):
    _shard(tmp_path, "shard_s15m_20261012", date(2026, 10, 12))
    assert _status(tmp_path)["shard_s15m_20261012"]["status"] == "never_checked"
    assert _run(tmp_path) == ["shard_s15m_20261012"]
    entry = _status(tmp_path)["shard_s15m_20261012"]
    assert (entry["status"], entry["scope"], entry["overdue"]) == ("verified", "full", False)
    assert _run(tmp_path, now=NOW + timedelta(hours=23)) == []  # not due yet
    assert _run(tmp_path, now=NOW + DAY) == ["shard_s15m_20261012"]
    assert not _status(tmp_path, NOW + 3 * DAY)["shard_s15m_20261012"]["overdue"]  # exactly 2x
    assert _status(tmp_path, NOW + 3 * DAY + timedelta(hours=1))["shard_s15m_20261012"]["overdue"]


def test_verification_is_idempotent_and_never_touches_artifacts(tmp_path):
    path = _shard(tmp_path, "shard_s15m_20261012", date(2026, 10, 12))
    before = _tree(path)
    for i in range(3):
        _run(tmp_path, now=NOW + i * DAY)
    assert _tree(path) == before
    assert {p.name for p in (tmp_path / "continuity").iterdir()} == {"verification.json"}


def test_work_per_pass_is_bounded_least_recently_checked_first(tmp_path):
    for k in range(5):
        _shard(tmp_path, f"shard_s15m_2026101{k}", date(2026, 10, 10 + k))
    assert len(_run(tmp_path, max_per_pass=2)) == 2
    assert len(_run(tmp_path, max_per_pass=2)) == 2
    assert _run(tmp_path, max_per_pass=2) == ["shard_s15m_20261014"]
    counts = integrity.verification_report(tmp_path, tmp_path / "continuity", NOW, DAY)["counts"]
    assert counts == {"verified": 5}


def test_an_interrupted_pass_keeps_completed_records_only(tmp_path):
    for k in range(3):
        _shard(tmp_path, f"shard_s15m_2026101{k}", date(2026, 10, 10 + k))
    calls = iter([False, True])
    assert _run(tmp_path, should_stop=lambda: next(calls)) == ["shard_s15m_20261010"]
    statuses = {k: v["status"] for k, v in _status(tmp_path).items()}
    assert statuses == {
        "shard_s15m_20261010": "verified",
        "shard_s15m_20261011": "never_checked",
        "shard_s15m_20261012": "never_checked",
    }
    # a restart simply continues
    assert _run(tmp_path) == ["shard_s15m_20261011", "shard_s15m_20261012"]


def test_a_changed_manifest_no_longer_counts_as_verified(tmp_path):
    path = _shard(tmp_path, "shard_s15m_20261012", date(2026, 10, 12))
    _run(tmp_path)
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["note"] = "changed"
    (path / "manifest.json").write_text(json.dumps(manifest))
    assert _status(tmp_path)["shard_s15m_20261012"]["status"] == "pending"
    assert _run(tmp_path, now=NOW + timedelta(minutes=1)) == ["shard_s15m_20261012"]


def test_missing_and_corrupted_artifacts_fail_with_reasons(tmp_path):
    gone = _shard(tmp_path, "shard_s15m_20261012", date(2026, 10, 12))
    corrupt = _shard(tmp_path, "shard_s15m_20261013", date(2026, 10, 13))
    short = _shard(tmp_path, "shard_s15m_20261014", date(2026, 10, 14))
    (gone / "data.parquet").unlink()
    with (corrupt / "data.parquet").open("ab") as handle:
        handle.write(b"tampered")
    manifest = json.loads((short / "manifest.json").read_text())
    manifest["row_count"] = 99
    (short / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "no-manifest").mkdir()
    _run(tmp_path)
    s = _status(tmp_path)
    assert (s["shard_s15m_20261012"]["status"], s["shard_s15m_20261012"]["error"]) == (
        "failed",
        "data file missing",
    )
    assert (
        s["shard_s15m_20261013"]["status"] == "failed"
        and "sha256" in s["shard_s15m_20261013"]["error"]
    )
    assert "rows, manifest records 99" in s["shard_s15m_20261014"]["error"]
    assert s["no-manifest"]["status"] == "unavailable"


def test_a_reserved_shard_is_hash_checked_only_and_never_parsed(tmp_path, monkeypatch):
    shutil.copytree(EXISTING, tmp_path / "evaluations" / EXISTING.name)
    _shard(tmp_path, "shard_s15m_20261027", date(2026, 10, 27))  # inside its validation
    _shard(tmp_path, "shard_s15m_20261012", date(2026, 10, 12))  # development

    parsed = []
    real = integrity.read_dataset

    def spy(path, columns=None):
        parsed.append(path.name)
        return real(path, columns=columns)

    monkeypatch.setattr(integrity, "read_dataset", spy)
    import pyarrow.parquet as pq

    real_read = pq.read_table

    def guarded(path, *args, **kwargs):
        assert "20261027" not in str(path), "a reserved shard must not be parsed"
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(pq, "read_table", guarded)
    _run(tmp_path)
    s = _status(tmp_path)
    assert (s["shard_s15m_20261027"]["status"], s["shard_s15m_20261027"]["scope"]) == (
        "restricted",
        "content_hash_only",
    )
    assert s["shard_s15m_20261012"]["status"] == "verified"
    assert parsed == ["shard_s15m_20261012"]
    assert not list((tmp_path / "evaluations").rglob("event.json"))  # no exposure


def test_a_tampered_reserved_shard_fails_on_its_hash(tmp_path):
    shutil.copytree(EXISTING, tmp_path / "evaluations" / EXISTING.name)
    path = _shard(tmp_path, "shard_s15m_20261027", date(2026, 10, 27))
    with (path / "data.parquet").open("ab") as handle:
        handle.write(b"x")
    _run(tmp_path)
    entry = _status(tmp_path)["shard_s15m_20261027"]
    assert (entry["status"], entry["scope"]) == ("failed", "content_hash_only")


# -- continuity alerts ---------------------------------------------------------------------


def _registration(
    phase="accumulating", start="2026-10-26", end="2026-11-25", verification="verified"
):
    return {
        "registration_id": "reg-v1",
        "verification": verification,
        "problems": [] if verification == "verified" else ["modified"],
        "partitions": [
            {
                "role": "validation",
                "start": start,
                "end": end,
                "phase": phase,
                "eligible_from": "2026-11-27T00:00:00+00:00",
            }
        ],
    }


def test_only_mature_unrecoverable_days_in_a_window_alert():
    days = [
        {"day": "2026-10-20", "status": "collection_gap"},  # before the window
        {"day": "2026-10-26", "status": "preserved"},
        {"day": "2026-10-27", "status": "collection_gap"},
        {"day": "2026-10-28", "status": "source_expired"},
        {"day": "2026-10-29", "status": "waiting_maturity"},  # immature: never an alert
    ]
    alerts = {a["condition"]: a for a in derive_alerts([_registration()], days, {})}
    lost = alerts["shard_unrecoverable"]
    assert lost["severity"] == "warning" and lost["days"] == ["2026-10-27", "2026-10-28"]
    assert (
        lost["interval"] == ["2026-10-27", "2026-10-28"]
        and lost["id"] == "reg-v1/validation/shard_unrecoverable"
    )
    assert alerts["awaiting_data"]["severity"] == "info"
    assert set(alerts) == {"shard_unrecoverable", "awaiting_data"}


def test_a_future_window_is_only_informational():
    alerts = derive_alerts(
        [_registration(phase="awaiting_window", start="2026-12-27", end="2027-01-26")],
        [{"day": "2026-10-24", "status": "collection_gap"}],
        {},
    )
    assert [(a["condition"], a["severity"]) for a in alerts] == [("awaiting_data", "info")]


def test_integrity_failures_and_invalid_registrations_are_critical():
    days = [
        {"day": "2026-10-26", "status": "preserved"},
        {"day": "2026-10-27", "status": "generate", "attempts": 3},
    ]
    verification = {"shard_s15m_20261026": {"status": "failed"}}
    alerts = {
        a["condition"]: a
        for a in derive_alerts(
            [_registration(phase="eligible", verification="invalid")], days, verification
        )
    }
    assert alerts["registration_invalid"]["severity"] == "critical"
    assert alerts["shard_integrity_failed"]["severity"] == "critical"
    assert alerts["shard_generation_failing"]["days"] == ["2026-10-27"]
    assert "awaiting_data" not in alerts


def test_multiple_registrations_are_kept_apart():
    second = {**_registration(), "registration_id": "other-v1"}
    days = [{"day": "2026-10-27", "status": "collection_gap"}]
    ids = {a["id"] for a in derive_alerts([_registration(), second], days, {})}
    assert {
        "reg-v1/validation/shard_unrecoverable",
        "other-v1/validation/shard_unrecoverable",
    } <= ids


def test_alert_lifecycle_deduplicates_resolves_and_reactivates(tmp_path):
    alert = derive_alerts(
        [_registration()], [{"day": "2026-10-27", "status": "collection_gap"}], {}
    )
    lost = [a for a in alert if a["condition"] == "shard_unrecoverable"]
    first = update_alerts(tmp_path, lost, NOW)
    again = update_alerts(tmp_path, lost, NOW + timedelta(minutes=30))
    assert len(again["active"]) == 1  # no duplicate
    assert again["active"][0]["first_observed"] == first["active"][0]["first_observed"]
    assert again["active"][0]["last_checked"] == (NOW + timedelta(minutes=30)).isoformat()
    cleared = update_alerts(tmp_path, [], NOW + timedelta(hours=1))
    assert cleared["active"] == [] and cleared["recently_resolved"][0]["state"] == "resolved"
    back = update_alerts(tmp_path, lost, NOW + timedelta(hours=2))
    assert back["active"][0]["occurrence"] == 2
    assert back["active"][0]["first_observed"] == (NOW + timedelta(hours=2)).isoformat()


def test_a_restart_does_not_resolve_anything(tmp_path):
    lost = [
        a
        for a in derive_alerts(
            [_registration()], [{"day": "2026-10-27", "status": "collection_gap"}], {}
        )
        if a["condition"] == "shard_unrecoverable"
    ]
    update_alerts(tmp_path, lost, NOW)
    # a new process reloads the stored lifecycle; the next pass sees the same condition
    after = update_alerts(tmp_path, lost, NOW + timedelta(hours=1))
    assert (
        after["active"][0]["first_observed"] == NOW.isoformat() and after["recently_resolved"] == []
    )


def test_a_stale_worker_is_an_alert_of_its_own(tmp_path):
    path = tmp_path / "status.json"
    report = {
        "written_at": NOW.isoformat(),
        "worker": {"heartbeat_at": NOW.isoformat()},
        "alerts": {
            "checked_at": NOW.isoformat(),
            "active": [{"id": "reg-v1/validation/awaiting_data", "severity": "info"}],
        },
    }
    path.write_text(json.dumps(report))
    fresh = research_status_from_file(path, NOW + timedelta(minutes=1), 1800)
    assert not fresh.alerts_stale and [a["id"] for a in fresh.alerts] == [
        "reg-v1/validation/awaiting_data"
    ]
    stale = research_status_from_file(path, NOW + timedelta(minutes=30), 1800)
    assert stale.alerts_stale and stale.alerts[0]["condition"] == "worker_stale"
    assert stale.alerts[0]["severity"] == "warning" and len(stale.alerts) == 2


# -- recent collection continuity ---------------------------------------------------------


def _frames(start, count, *, analysis="v2", status="complete", members=10):
    return [(start + i * FIVE, status, members, members, analysis) for i in range(count)]


def test_a_complete_day_is_full_coverage_and_full_analysis():
    start = NOW - DAY
    now = NOW + timedelta(minutes=2, seconds=30)  # due through NOW
    out = recent_continuity(_frames(start, 289), now)["windows"]["24h"]
    assert (out["expected"], out["finalized"], out["missing"], out["coverage"]) == (
        288,
        288,
        0,
        1.0,
    )
    assert out["analysis"]["completion"] == 1.0 and out["window_shortened"] is False


def test_collection_and_analysis_are_reported_separately():
    start = NOW - timedelta(hours=1)
    rows = (
        _frames(start, 6)
        + _frames(start + 6 * FIVE, 3, analysis=None)
        + _frames(start + 9 * FIVE, 3)
    )
    rows = [r for i, r in enumerate(rows) if i not in (10,)]  # one missing frame
    now = NOW + timedelta(minutes=10)
    out = recent_continuity(rows, now)["windows"]["24h"]
    assert out["window_shortened"] is True  # collection began inside the window
    # 14 due slots (NOW-60m .. NOW+5m), 11 finalized: 1 gap + 2 after collection stopped
    assert (out["expected"], out["finalized"], out["missing"]) == (14, 11, 3)
    assert out["analysis"]["analyzed"] == 8 and out["analysis"]["finalized_with_members"] == 11
    assert out["analysis"]["completion"] == pytest.approx(8 / 11)


def test_the_forming_frame_and_frames_still_being_analyzed_are_excluded():
    start = NOW - timedelta(minutes=30)
    rows = _frames(start, 6) + [(NOW, "building", 10, 0, None)]
    rows += [(NOW - FIVE, "complete", 10, 10, None)]  # finalized, analysis not yet due
    rows = sorted({r[0]: r for r in rows}.values())
    # due through NOW-5m; its analysis is allowed until NOW-5m + 60s + 2m
    out = recent_continuity(rows, NOW - FIVE + timedelta(minutes=2, seconds=30))["windows"]["24h"]
    assert out["to"] == NOW.isoformat()  # NOW's frame is not yet due
    assert out["analysis"]["pending"] == 1 and out["never_finalized"] == 0


def test_seven_day_windows_aggregate_across_utc_days():
    start = datetime(2026, 10, 20, 23, tzinfo=UTC)
    rows = _frames(start, 24) + _frames(start + timedelta(days=2), 12)
    now = start + timedelta(days=3)
    week = recent_continuity(rows, now)["windows"]["7d"]
    assert week["finalized"] == 36 and week["from"] == start.isoformat()
    assert week["expected"] == int((now - timedelta(minutes=2) - start) / FIVE) + 1


def test_no_frames_is_unavailable():
    assert recent_continuity([], NOW) == {
        "available": False,
        "reason": "no frame has been recorded yet",
    }
