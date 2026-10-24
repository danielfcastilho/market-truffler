import json
from datetime import UTC, datetime, timedelta

import pytest
from research_helpers import at, write_dataset

from app.research import backup as backup_module
from app.research.backup import (
    CHECKSUMS,
    MARKER,
    BackupIntegrityError,
    backup_report,
    research_items,
    restore_item,
    run_backup,
    verify_copy,
)
from app.research.dataset import read_dataset

NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)


@pytest.fixture
def data_dir(tmp_path):
    data = tmp_path / "data"
    write_dataset(data / "shard_s15m_20261012", [{"frame_time": at(1), "symbol": "A"}])
    run = data / "runs" / "exp-v1" / "20261012T000000Z-abc"
    run.mkdir(parents=True)
    (run / "spec.json").write_text('{"id": "exp-v1"}')
    (run / "result.json").write_text('{"verdict": "inconclusive"}')
    (data / ".shard_s15m_20261013.tmp-dead").mkdir()  # never an item
    (data / "continuity").mkdir()
    (data / "continuity" / "status.json").write_text("{}")
    return data


@pytest.fixture
def destination(tmp_path):
    dest = tmp_path / "backup"
    dest.mkdir()
    (dest / MARKER).write_text("")
    return dest


def test_items_are_datasets_and_run_records_only(data_dir):
    assert [str(p) for p in research_items(data_dir)] == [
        "runs/exp-v1/20261012T000000Z-abc",
        "shard_s15m_20261012",
    ]


def test_backup_copies_and_verifies_every_record(data_dir, destination):
    report = run_backup(data_dir, destination, now=NOW)
    assert report["error"] is None and report["errors"] == {}
    assert sorted(report["items"]) == ["runs/exp-v1/20261012T000000Z-abc", "shard_s15m_20261012"]
    copy = destination / "shard_s15m_20261012"
    assert (copy / "data.parquet").read_bytes() == (
        data_dir / "shard_s15m_20261012" / "data.parquet"
    ).read_bytes()
    verify_copy(copy)
    assert not list(destination.glob(".*.tmp-*"))
    persisted = json.loads((data_dir / "continuity" / "backup.json").read_text())
    assert persisted["checked_at"] == NOW.isoformat()
    summary = backup_report(data_dir, configured=True)
    assert summary["protected"] == sorted(report["items"])


@pytest.mark.parametrize("marker", [False, True])
def test_an_unmounted_or_uninitialized_destination_writes_nothing(data_dir, tmp_path, marker):
    dest = tmp_path / "not-mounted"
    if marker:  # exists, but nobody confirmed it is independent storage
        dest.mkdir()
    report = run_backup(data_dir, dest, now=NOW)
    assert report["error"] and report["items"] == {}
    assert not dest.exists() or list(dest.iterdir()) == []
    assert backup_report(data_dir, configured=True)["protected"] == []


def test_a_destination_inside_the_data_directory_is_refused(data_dir):
    inside = data_dir / "backup"
    inside.mkdir()
    (inside / MARKER).write_text("")
    report = run_backup(data_dir, inside, now=NOW)
    assert "overlaps" in report["error"] and report["items"] == {}


def test_not_configured_is_reported_as_none_never_as_protected(data_dir):
    assert backup_report(data_dir, configured=False) is None


def test_an_existing_differing_backup_is_never_overwritten(data_dir, destination):
    run_backup(data_dir, destination, now=NOW)
    copy = destination / "runs" / "exp-v1" / "20261012T000000Z-abc" / "result.json"
    copy.write_text('{"verdict": "tampered"}')
    report = run_backup(data_dir, destination, now=NOW, reverify_all=True)
    assert "runs/exp-v1/20261012T000000Z-abc" in report["errors"]
    assert copy.read_text() == '{"verdict": "tampered"}'  # left for a human
    assert "runs/exp-v1/20261012T000000Z-abc" not in report["items"]


def test_a_corrupt_source_dataset_is_reported_and_not_copied(data_dir, destination):
    with (data_dir / "shard_s15m_20261012" / "data.parquet").open("r+b") as handle:
        handle.seek(10)
        handle.write(b"\x00\x01")
    report = run_backup(data_dir, destination, now=NOW)
    assert "doesn't match its manifest" in report["errors"]["shard_s15m_20261012"]
    assert not (destination / "shard_s15m_20261012").exists()


@pytest.mark.parametrize("damage", ["flip", "truncate", "delete", "extra"])
def test_verification_detects_corrupt_or_incomplete_copies(data_dir, destination, damage):
    run_backup(data_dir, destination, now=NOW)
    copy = destination / "shard_s15m_20261012"
    target = copy / "data.parquet"
    target.chmod(0o644)
    if damage == "flip":
        raw = bytearray(target.read_bytes())
        raw[20] ^= 0xFF
        target.write_bytes(bytes(raw))
    elif damage == "truncate":
        target.write_bytes(target.read_bytes()[:-10])
    elif damage == "delete":
        target.unlink()
    else:
        (copy / "stray.txt").write_text("?")
    with pytest.raises(BackupIntegrityError):
        verify_copy(copy)
    report = run_backup(data_dir, destination, now=NOW, reverify_all=True)
    assert "shard_s15m_20261012" in report["errors"]


def test_protected_copies_are_reverified_only_when_due(data_dir, destination, monkeypatch):
    run_backup(data_dir, destination, now=NOW)
    hashed = []
    real = backup_module._file_hashes
    monkeypatch.setattr(backup_module, "_file_hashes", lambda d: hashed.append(d) or real(d))
    run_backup(data_dir, destination, now=NOW + timedelta(hours=1))
    assert hashed == []
    run_backup(data_dir, destination, now=NOW + backup_module.REVERIFY_INTERVAL)
    assert hashed  # due again: both sides re-hashed


def test_restore_after_losing_the_data_directory(data_dir, destination, tmp_path):
    original = read_dataset(data_dir / "shard_s15m_20261012")[0]
    run_backup(data_dir, destination, now=NOW)
    fresh = tmp_path / "new-disk"
    for name in ("shard_s15m_20261012", "runs/exp-v1/20261012T000000Z-abc"):
        restore_item(destination, fresh, name)
    manifest, table = read_dataset(fresh / "shard_s15m_20261012")
    assert manifest == original and table.num_rows == 1
    assert not (fresh / "shard_s15m_20261012" / CHECKSUMS).exists()
    assert (fresh / "runs/exp-v1/20261012T000000Z-abc/result.json").read_text() == (
        '{"verdict": "inconclusive"}'
    )


def test_restore_never_replaces_existing_data(data_dir, destination):
    run_backup(data_dir, destination, now=NOW)
    with pytest.raises(FileExistsError):
        restore_item(destination, data_dir, "shard_s15m_20261012")


def test_restore_refuses_a_corrupt_backup(data_dir, destination, tmp_path):
    run_backup(data_dir, destination, now=NOW)
    target = destination / "shard_s15m_20261012" / "data.parquet"
    target.chmod(0o644)
    target.write_bytes(target.read_bytes()[:-1])
    fresh = tmp_path / "new-disk"
    with pytest.raises(BackupIntegrityError):
        restore_item(destination, fresh, "shard_s15m_20261012")
    assert not (fresh / "shard_s15m_20261012").exists()


def test_backup_never_uploads():
    """Only local, operator-mounted paths: no network code."""
    source = open(backup_module.__file__).read()
    for forbidden in ("http", "boto", "requests", "socket", "upload(", "rsync"):
        assert forbidden not in source
