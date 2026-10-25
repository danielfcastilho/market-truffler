"""Independent research backups: scope, completion vs verification,
isolated recovery tests, backup alerts, and protected-data restrictions."""

import json
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from research_helpers import write_dataset

from app.research import backup as b
from app.research.research_alerts import derive_backup_alerts, update_alerts
from app.services.research_data_health import backup_summary

REPO = Path(__file__).resolve().parents[3]
EXISTING = REPO / "research/oink_corp/data/evaluations/oi-price-1h-prospective-v1"
NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)
DAY = timedelta(hours=24)


def _shard(data: Path, name: str, day: date) -> Path:
    t = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    write_dataset(
        data / name,
        [{"frame_time": t + timedelta(minutes=15 * i), "symbol": "S"} for i in range(4)],
        sample_interval_minutes=15,
    )
    return data / name


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


@pytest.fixture
def lab(tmp_path):
    data = tmp_path / "data"
    _shard(data, "shard_s15m_20261012", date(2026, 10, 12))
    _shard(data, "shard_s15m_20261027", date(2026, 10, 27))  # inside the reserved validation
    shutil.copytree(EXISTING, data / "evaluations" / EXISTING.name)
    event = data / "evaluations" / EXISTING.name / "events" / "000001"
    event.mkdir(parents=True)
    (event / "event.json").write_text('{"type": "authorized"}')
    run = data / "runs" / "exp-v1" / "20261012T000000Z-abc"
    run.mkdir(parents=True)
    (run / "result.json").write_text("{}")
    (data / ".shard_s15m_20261028.tmp-dead").mkdir()  # being published: never an item
    (data / "continuity").mkdir()
    (data / "continuity" / "status.json").write_text("{}")
    dest = tmp_path / "backup"
    dest.mkdir()
    (dest / b.MARKER).write_text("")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return {"data": data, "dest": dest, "scratch": scratch}


# -- scope ----------------------------------------------------------------------------------


def test_scope_is_the_critical_scientific_state_only(lab):
    items = [str(p) for p in b.research_items(lab["data"])]
    assert items == [
        "evaluations/oi-price-1h-prospective-v1/registration",
        "evaluations/oi-price-1h-prospective-v1/events/000001",
        "runs/exp-v1/20261012T000000Z-abc",
        "shard_s15m_20261012",
        "shard_s15m_20261027",
    ]
    # temporary (being published) and operational (continuity) state excluded


def test_a_missing_optional_directory_is_simply_absent(tmp_path):
    data = tmp_path / "data"
    _shard(data, "shard_s15m_20261012", date(2026, 10, 12))
    assert [str(p) for p in b.research_items(data)] == ["shard_s15m_20261012"]
    assert list(b.research_items(tmp_path / "nowhere")) == []


# -- completion vs verification ----------------------------------------------------------


def test_a_full_run_records_completion_verification_counts_and_bytes(lab):
    before = _tree(lab["data"] / "evaluations")
    report = b.run_backup(lab["data"], lab["dest"], now=NOW)
    assert report["errors"] == {} and report["last_success_at"] == NOW.isoformat()
    assert report["items_in_scope"] == 5 and report["bytes"] > 0
    summary = b.backup_report(lab["data"], configured=True)
    assert summary["unprotected"] == [] and summary["items_in_scope"] == 5
    assert _tree(lab["data"] / "evaluations") == before  # registrations and exposure logs untouched
    # idempotent: a second run copies nothing and re-verifies nothing early
    again = b.run_backup(lab["data"], lab["dest"], now=NOW + timedelta(hours=1))
    assert again["items"] == report["items"] and again["last_success_at"] == again["checked_at"]


def test_an_interrupted_copy_leaves_no_partial_item_and_is_retried(lab, monkeypatch):
    real = shutil.copytree
    calls = {"n": 0}

    def failing(src, dst, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk unplugged")
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(b.shutil, "copytree", failing)
    first = b.run_backup(lab["data"], lab["dest"], now=NOW)
    assert len(first["errors"]) == 1 and first["last_success_at"] is None
    assert not list(lab["dest"].rglob(".*.tmp-*"))
    summary = b.backup_report(lab["data"], configured=True)
    assert len(summary["unprotected"]) == 1
    retried = b.run_backup(lab["data"], lab["dest"], now=NOW + timedelta(minutes=30))
    assert retried["errors"] == {} and retried["last_success_at"] is not None


def test_a_corrupt_copy_is_an_integrity_failure_never_overwritten(lab):
    b.run_backup(lab["data"], lab["dest"], now=NOW)
    copy = lab["dest"] / "shard_s15m_20261012" / "data.parquet"
    copy.write_bytes(copy.read_bytes() + b"x")
    report = b.run_backup(lab["data"], lab["dest"], now=NOW + 2 * DAY)  # re-verification due
    assert report["integrity_errors"] == ["shard_s15m_20261012"]
    assert copy.read_bytes().endswith(b"x")  # reported, not repaired


def test_an_unconfigured_or_missing_destination_is_reported(lab, tmp_path):
    missing = b.run_backup(lab["data"], tmp_path / "unmounted", now=NOW)
    assert "does not exist" in missing["error"]
    assert b.backup_report(lab["data"], configured=False) is None


# -- recovery test ---------------------------------------------------------------------------


def test_a_recovery_test_restores_in_isolation_and_cleans_up(lab):
    b.run_backup(lab["data"], lab["dest"], now=NOW)
    live = _tree(lab["data"])
    result = b.recovery_test(lab["data"], lab["dest"], now=NOW, scratch=lab["scratch"])
    assert result["status"] == "passed" and result["scope"] == "full"
    assert result["items_tested"] == 5 and result["bytes_restored"] > 0
    assert result["restricted_items"] == 1  # the reserved shard: hash only
    assert list(lab["scratch"].iterdir()) == []  # temporary restore removed
    after = _tree(lab["data"])
    after.pop("continuity/recovery_test.json")
    after.pop("continuity/backup.json", None)
    live.pop("continuity/backup.json", None)
    assert after == live  # nothing restored over live data


def test_a_corrupt_or_missing_backup_fails_the_recovery_test(lab):
    b.run_backup(lab["data"], lab["dest"], now=NOW)
    shutil.rmtree(lab["dest"] / "runs" / "exp-v1" / "20261012T000000Z-abc")
    corrupt = lab["dest"] / "shard_s15m_20261012" / "manifest.json"
    corrupt.write_text(corrupt.read_text() + " ")
    result = b.recovery_test(lab["data"], lab["dest"], now=NOW, scratch=lab["scratch"])
    assert result["status"] == "failed"
    assert set(result["failures"]) == {"runs/exp-v1/20261012T000000Z-abc", "shard_s15m_20261012"}
    assert list(lab["scratch"].iterdir()) == []


def test_a_large_archive_gets_a_representative_test_reported_as_such(lab):
    b.run_backup(lab["data"], lab["dest"], now=NOW)
    result = b.recovery_test(lab["data"], lab["dest"], now=NOW, scratch=lab["scratch"], max_bytes=1)
    assert result["scope"] == "representative"
    assert result["items_tested"] == 2  # the registration and its exposure event


def test_recovery_needs_a_verified_backup_first(lab):
    result = b.recovery_test(lab["data"], lab["dest"], now=NOW, scratch=lab["scratch"])
    assert result["status"] == "unavailable" and "no verified backup" in result["error"]


def test_recovery_test_scheduling(lab):
    assert b.recovery_test_due(lab["data"], NOW, DAY)
    b.run_backup(lab["data"], lab["dest"], now=NOW)
    b.recovery_test(lab["data"], lab["dest"], now=NOW, scratch=lab["scratch"])
    assert not b.recovery_test_due(lab["data"], NOW + timedelta(hours=23), DAY)
    assert b.recovery_test_due(lab["data"], NOW + DAY, DAY)


def test_protected_outcomes_are_never_parsed_on_backup_or_recovery(lab, monkeypatch):
    import pyarrow.parquet as pq

    real = pq.read_table

    def guarded(path, *args, **kwargs):
        assert "20261027" not in str(path), "a reserved shard must not be parsed"
        return real(path, *args, **kwargs)

    monkeypatch.setattr(pq, "read_table", guarded)
    b.run_backup(lab["data"], lab["dest"], now=NOW)
    assert (
        b.recovery_test(lab["data"], lab["dest"], now=NOW, scratch=lab["scratch"])["status"]
        == "passed"
    )
    # an operator restore of a reserved shard is verified by hash, not parsed
    shutil.rmtree(lab["data"] / "shard_s15m_20261027")
    b.restore_item(lab["dest"], lab["data"], "shard_s15m_20261027")
    assert not list((lab["data"] / "evaluations").rglob("000002"))  # no new exposure event


def test_restore_never_overwrites_live_data(lab):
    b.run_backup(lab["data"], lab["dest"], now=NOW)
    with pytest.raises(FileExistsError):
        b.restore_item(
            lab["dest"], lab["data"], "evaluations/oi-price-1h-prospective-v1/registration"
        )


# -- monitoring ------------------------------------------------------------------------------


def _conditions(backup, now=NOW):
    return {a["condition"]: a["severity"] for a in derive_backup_alerts(backup, now, DAY)}


def test_backup_alert_conditions_and_severities(lab):
    assert _conditions(None) == {"backup_not_configured": "warning"}
    b.run_backup(lab["data"], lab["dest"], now=NOW)
    healthy = b.backup_report(lab["data"], configured=True)
    assert _conditions(healthy) == {"recovery_untested": "warning"}
    b.recovery_test(lab["data"], lab["dest"], now=NOW, scratch=lab["scratch"])
    assert _conditions(b.backup_report(lab["data"], configured=True)) == {}
    later = _conditions(b.backup_report(lab["data"], configured=True), NOW + 2 * DAY)
    assert later == {"backup_overdue": "warning"}
    never = {**healthy, "error": "not mounted", "last_success_at": None}
    assert _conditions(never)["backup_destination_unavailable"] == "critical"
    lost = {**healthy, "error": "not mounted"}
    assert _conditions(lost)["backup_destination_unavailable"] == "warning"
    corrupt = {
        **healthy,
        "integrity_errors": ["shard_s15m_20261012"],
        "errors": {"shard_s15m_20261012": "x"},
    }
    assert _conditions(corrupt)["backup_integrity_failed"] == "critical"
    failed_test = {**healthy, "recovery_test": {"status": "failed", "failures": {"a": "b"}}}
    assert _conditions(failed_test)["recovery_test_failed"] == "critical"


def test_backup_alerts_resolve_and_survive_restarts(tmp_path):
    alerts = derive_backup_alerts(None, NOW, DAY)
    first = update_alerts(tmp_path, alerts, NOW)
    again = update_alerts(tmp_path, alerts, NOW + timedelta(hours=1))  # e.g. after a restart
    assert again["active"][0]["first_observed"] == first["active"][0]["first_observed"]
    cleared = update_alerts(tmp_path, [], NOW + timedelta(hours=2))
    assert (
        cleared["active"] == []
        and cleared["recently_resolved"][0]["id"] == "research_backup/-/backup_not_configured"
    )


def test_data_health_reports_four_separate_states_without_the_path(lab):
    assert backup_summary(None)["available"] is False
    unconfigured = backup_summary({"backup": None})
    assert unconfigured["configured"] is False and "RESEARCH_BACKUP_DIR" in unconfigured["reason"]
    b.run_backup(lab["data"], lab["dest"], now=NOW)
    b.recovery_test(lab["data"], lab["dest"], now=NOW, scratch=lab["scratch"])
    summary = backup_summary({"backup": b.backup_report(lab["data"], configured=True)})
    assert summary["configured"] and summary["last_attempt_at"] == NOW.isoformat()
    assert summary["last_success_at"] == NOW.isoformat()
    assert summary["recovery_test"]["status"] == "passed"
    assert (summary["protected"], summary["unprotected"]) == (5, 0)
    assert str(lab["dest"]) not in json.dumps(summary)
