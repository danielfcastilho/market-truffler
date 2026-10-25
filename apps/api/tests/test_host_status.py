import json
from datetime import UTC, datetime, timedelta

import pytest

from app.research.research_alerts import derive_worker_alerts
from app.services.host_status import FILE_NAME, STALE_AFTER, read_sleep_protection

NOW = datetime(2026, 10, 25, 17, 45, tzinfo=UTC)


def _report(directory, **fields):
    report = {
        "schema": 1,
        "observed_at": "2026-10-25T17:44:30Z",
        "state": "active",
        "detail": "sleep-prevention assertions registered",
        "power_source": "AC Power",
        "interval_seconds": 60,
        **fields,
    }
    (directory / FILE_NAME).write_text(json.dumps(report))


def test_a_fresh_active_report_is_active(tmp_path):
    _report(tmp_path)
    protection = read_sleep_protection(tmp_path, NOW)
    assert protection.state == "active"
    assert protection.observed_at == datetime(2026, 10, 25, 17, 44, 30, tzinfo=UTC)
    assert protection.power_source == "AC Power"


@pytest.mark.parametrize("reported", ["inactive", "stopped"])
def test_a_fresh_inactive_or_stopped_report_is_inactive(tmp_path, reported):
    _report(tmp_path, state=reported, detail="stopped by the operator")
    protection = read_sleep_protection(tmp_path, NOW)
    assert protection.state == "inactive" and protection.reported_state == reported


def test_a_stale_active_report_is_unknown_never_active(tmp_path):
    observed = NOW - STALE_AFTER - timedelta(seconds=1)
    _report(tmp_path, observed_at=observed.isoformat())
    protection = read_sleep_protection(tmp_path, NOW)
    assert protection.state == "unknown"
    assert protection.reported_state == "active"
    assert "no host report since" in protection.detail


def test_a_missing_report_is_unknown(tmp_path):
    protection = read_sleep_protection(tmp_path, NOW)
    assert protection.state == "unknown" and protection.observed_at is None


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps({"schema": 1, "state": "active"}),
        json.dumps({"schema": 2, "state": "active", "observed_at": "2026-10-25T17:44:30Z"}),
        json.dumps({"schema": 1, "state": "awake", "observed_at": "2026-10-25T17:44:30Z"}),
        json.dumps({"schema": 1, "state": "active", "observed_at": "2026-10-25T17:44:30"}),
        "{" + " " * 5000 + "}",
    ],
)
def test_a_malformed_report_is_unknown(tmp_path, content):
    (tmp_path / FILE_NAME).write_text(content)
    assert read_sleep_protection(tmp_path, NOW).state == "unknown"


def test_a_report_from_the_future_is_unknown(tmp_path):
    _report(tmp_path, observed_at=(NOW + timedelta(minutes=5)).isoformat())
    assert read_sleep_protection(tmp_path, NOW).state == "unknown"


async def test_system_info_reports_host_sleep_protection(client, test_user, tmp_path, monkeypatch):
    monkeypatch.setenv("HOST_STATUS_DIR", str(tmp_path))
    _report(tmp_path, observed_at=datetime.now(UTC).isoformat())
    await client.post(
        "/api/auth/login",
        json={"email": test_user.email, "password": "correct-horse-battery"},
    )
    response = await client.get("/api/system")
    assert response.status_code == 200
    protection = response.json()["host_sleep_protection"]
    assert protection["state"] == "active"
    assert protection["stale_after_seconds"] == int(STALE_AFTER.total_seconds())


def test_a_lost_generation_lock_raises_a_worker_alert_until_a_clean_pass():
    alerts = derive_worker_alerts({"last_pass_summary": {"lock_lost": True}})
    assert [a["id"] for a in alerts] == ["research_worker/-/generation_lock_lost"]
    assert alerts[0]["severity"] == "warning"
    assert derive_worker_alerts({"last_pass_summary": {"lock_lost": False}}) == []
    assert derive_worker_alerts({}) == []
