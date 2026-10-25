"""OINK CORP research data backup: copy every immutable research record —
dataset directories (`<name>/data.parquet` + `manifest.json`) and experiment
run records (`runs/<experiment>/<run>/`) — to an operator-provided
destination, and verify every copy before calling it protected.

The destination is a local path the operator mounts (another physical
disk, a NAS share, a synced folder): this module never uploads anywhere and
never picks a destination itself. Whether that path is *independent* of
the research data's disk is something only the operator can know — so a
destination must be explicitly initialized by creating the marker file
`.market-truffler-backup` in it. Without the marker nothing is written
(an unmounted volume would otherwise silently become a directory inside
the container, which protects nothing).

Rules:

- A backup copy is written to `.<name>.tmp-*`, every file's sha256 is
  compared with the source's, and only then is it renamed into place.
  A dataset's source is first checked against its own `data_sha256`; a
  corrupt source is reported and never copied.
- An existing backup copy is never overwritten. If it doesn't match the
  source it is reported as a conflict for a human to resolve.
- Each copy carries `BACKUP_CHECKSUMS.json` (sha256 of every file), so a
  backup can be verified and restored without the original data
  directory — including after losing the disk it lived on.
- Copies already protected are re-verified (re-hashed on both sides) at
  most every `REVERIFY_INTERVAL`, so a pass doesn't re-read everything.
- Restore copies a verified backup into the data directory under the same
  atomic-rename rules; it refuses to replace anything that exists.

The last report is persisted to `<data_dir>/continuity/backup.json` and
embedded in the continuity status — Vitals shows that, nothing inferred.
"""

import json
import logging
import os
import shutil
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.research.continuity import CONTINUITY_DIRNAME, _write_json_atomic
from app.research.dataset import file_sha256, read_dataset
from app.research.reservations import EVALUATIONS_DIRNAME, ReservedDataError, check_access

logger = logging.getLogger(__name__)

MARKER = ".market-truffler-backup"
CHECKSUMS = "BACKUP_CHECKSUMS.json"
REPORT = "backup.json"
REVERIFY_INTERVAL = timedelta(hours=24)
RECOVERY_REPORT = "recovery_test.json"
#: A recovery test restores every protected item while the archive is
#: below this size; above it, a bounded representative sample (reported as
#: such, never as a full disaster-recovery test).
FULL_RECOVERY_MAX_BYTES = 2 * 1024**3
RUNS_DIRNAME = "runs"


class BackupError(RuntimeError):
    """The destination can't be used at all (missing, not initialized, or
    overlapping the data directory)."""


class BackupIntegrityError(ValueError):
    """A copy (or a source) doesn't match the checksums it must match."""


# -- discovery and hashing ----------------------------------------------------------------------


def research_items(data_dir: Path) -> Iterator[Path]:
    """Every immutable research record, as a path relative to `data_dir`:
    top-level dataset directories (those with a `manifest.json`) and run
    record directories under `runs/<experiment>/`, and prospective
    evaluation registrations and exposure events under `evaluations/`.
    Temporary (`.`-prefixed)
    directories and the operational `continuity/` state are never items."""
    if not data_dir.exists():
        return
    for path in sorted(data_dir.iterdir()):
        if not path.is_dir() or path.name.startswith(".") or path.name == CONTINUITY_DIRNAME:
            continue
        if path.name == RUNS_DIRNAME:
            for experiment in sorted(p for p in path.iterdir() if p.is_dir()):
                for run in sorted(p for p in experiment.iterdir() if p.is_dir()):
                    if not run.name.startswith("."):
                        yield run.relative_to(data_dir)
        elif path.name == EVALUATIONS_DIRNAME:
            # Prospective registrations and their exposure events: each an
            # immutable directory of its own (see app.research.prospective).
            for registration in sorted(p for p in path.iterdir() if p.is_dir()):
                if registration.name.startswith("."):
                    continue
                if (registration / "registration").is_dir():
                    yield (registration / "registration").relative_to(data_dir)
                events = registration / "events"
                for event in sorted(events.iterdir()) if events.is_dir() else []:
                    if event.is_dir() and not event.name.startswith("."):
                        yield event.relative_to(data_dir)
        elif (path / "manifest.json").exists():
            yield path.relative_to(data_dir)


def _file_hashes(directory: Path) -> dict[str, str]:
    return {
        str(p.relative_to(directory)): file_sha256(p)
        for p in sorted(directory.rglob("*"))
        if p.is_file() and p.name != CHECKSUMS
    }


def _check_source(source: Path) -> dict[str, str]:
    """Hashes of a source item; a dataset must also match its own manifest."""
    hashes = _file_hashes(source)
    if (source / "manifest.json").exists() and (source / "data.parquet").exists():
        recorded = json.loads((source / "manifest.json").read_text()).get("data_sha256")
        if hashes.get("data.parquet") != recorded:
            raise BackupIntegrityError(
                f"source {source.name}: data.parquet doesn't match its manifest"
            )
    return hashes


def verify_copy(copy: Path, expected: dict[str, str] | None = None) -> dict[str, str]:
    """Verify a backup copy against its own `BACKUP_CHECKSUMS.json` (and,
    if given, the expected source hashes): every listed file present and
    matching, nothing extra, nothing missing. Returns the hashes."""
    try:
        recorded = json.loads((copy / CHECKSUMS).read_text())["files"]
    except (OSError, ValueError, KeyError) as exc:
        raise BackupIntegrityError(f"{copy}: unreadable {CHECKSUMS}: {exc}") from exc
    actual = _file_hashes(copy)
    if actual != recorded:
        bad = sorted(set(actual) ^ set(recorded)) or sorted(
            k for k in actual if actual[k] != recorded.get(k)
        )
        raise BackupIntegrityError(f"{copy}: files missing, extra or corrupt: {bad}")
    if expected is not None and actual != expected:
        raise BackupIntegrityError(f"{copy}: differs from the source")
    return actual


# -- destination ------------------------------------------------------------------------------


def check_destination(data_dir: Path, destination: Path) -> None:
    if not destination.is_dir():
        raise BackupError(f"backup destination {destination} does not exist (not mounted?)")
    if not (destination / MARKER).is_file():
        raise BackupError(
            f"backup destination {destination} is not initialized: create {MARKER} in it "
            "once you have confirmed it is on independent storage"
        )
    data, dest = data_dir.resolve(), destination.resolve()
    if data == dest or data in dest.parents or dest in data.parents:
        raise BackupError(f"backup destination {destination} overlaps the data directory")


def _copy_item(source: Path, target: Path, hashes: dict[str, str]) -> None:
    """Copy into a temporary sibling, verify, then rename into place."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.tmp-{uuid4().hex[:8]}"
    try:
        shutil.copytree(source, tmp)
        (tmp / CHECKSUMS).write_text(json.dumps({"files": hashes}, indent=2, sort_keys=True))
        for path in tmp.rglob("*"):
            if path.is_file():
                with path.open("rb") as handle:
                    os.fsync(handle.fileno())
        verify_copy(tmp, hashes)
        if target.exists():
            raise BackupIntegrityError(f"{target} appeared while copying; not overwritten")
        tmp.rename(target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


# -- the backup step ---------------------------------------------------------------------------


def _report_path(data_dir: Path) -> Path:
    return data_dir / CONTINUITY_DIRNAME / REPORT


def load_report(data_dir: Path) -> dict[str, Any]:
    try:
        report = json.loads(_report_path(data_dir).read_text())
        if isinstance(report, dict):
            return report
    except (OSError, ValueError):
        pass
    return {}


def run_backup(
    data_dir: Path, destination: Path, *, now: datetime | None = None, reverify_all: bool = False
) -> dict[str, Any]:
    """Back up every research item not yet protected, re-verify protected
    ones that are due, persist and return the report. Never raises for a
    single item; a destination that can't be used is reported as an error."""
    now = now or datetime.now(UTC)
    last = load_report(data_dir)
    # Earlier verifications only count for the same destination.
    previous = last.get("items", {}) if last.get("destination") == str(destination) else {}
    report: dict[str, Any] = {
        "destination": str(destination),
        "checked_at": now.isoformat(),
        "items": {},
        "errors": {},
        # Items whose copy (or source) does not match its checksums — the
        # integrity failures, as opposed to I/O errors.
        "integrity_errors": [],
        "error": None,
        # Carried forward: the last run that copied and verified everything.
        "last_success_at": last.get("last_success_at") if previous else None,
    }
    try:
        check_destination(data_dir, destination)
    except BackupError as exc:
        report["error"] = str(exc)
        _write_json_atomic(_report_path(data_dir), report)
        logger.warning("research_backup_unavailable", extra={"error": str(exc)})
        return report

    for rel in research_items(data_dir):
        name = str(rel)
        source, target = data_dir / rel, destination / rel
        prior = previous.get(name)
        try:
            if (
                prior
                and not reverify_all
                and target.exists()
                and now - datetime.fromisoformat(prior["verified_at"]) < REVERIFY_INTERVAL
            ):
                report["items"][name] = prior
                continue
            hashes = _check_source(source)
            if target.exists():
                verify_copy(target, hashes)  # never overwritten, only checked
            else:
                _copy_item(source, target, hashes)
            report["items"][name] = {"verified_at": now.isoformat(), "files": hashes}
        except (BackupIntegrityError, OSError, ValueError) as exc:
            report["errors"][name] = str(exc)
            if isinstance(exc, BackupIntegrityError):
                report["integrity_errors"].append(name)
            logger.warning("research_backup_item_failed", extra={"item": name, "error": str(exc)})
    report["items_in_scope"] = len(report["items"]) + len(report["errors"])
    report["bytes"] = sum(
        (data_dir / name / f).stat().st_size
        for name, item in report["items"].items()
        for f in item.get("files", {})
        if (data_dir / name / f).exists()
    )
    if not report["errors"]:
        report["last_success_at"] = now.isoformat()
    _write_json_atomic(_report_path(data_dir), report)
    return report


def backup_report(data_dir: Path, configured: bool) -> dict[str, Any] | None:
    """The summary embedded in the continuity status: what is protected
    *as of the last verified backup run*. None when not configured."""
    if not configured:
        return None
    report = load_report(data_dir)
    in_scope = [str(p) for p in research_items(data_dir)]
    protected = sorted(report.get("items", {}))
    return {
        "destination": report.get("destination"),
        # Only the kind is shown to users, never the path.
        "destination_kind": "operator-initialized mount",
        "checked_at": report.get("checked_at"),
        "last_success_at": report.get("last_success_at"),
        "error": report.get("error"),
        "errors": report.get("errors", {}),
        "integrity_errors": report.get("integrity_errors", []),
        "protected": protected,
        "items_in_scope": len(in_scope),
        "unprotected": sorted(set(in_scope) - set(protected)),
        "bytes": report.get("bytes"),
        "recovery_test": load_recovery_test(data_dir),
    }


# -- restore -------------------------------------------------------------------------------------


def restore_item(destination: Path, data_dir: Path, name: str) -> Path:
    """Restore one item (`<dataset>` or `runs/<experiment>/<run>`) from a
    verified backup copy. Refuses to replace anything that exists; a
    restored dataset must also pass `read_dataset`'s full verification."""
    copy = destination / name
    target = data_dir / name
    if target.exists():
        raise FileExistsError(f"{target} exists — restore never replaces research data")
    hashes = verify_copy(copy)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.tmp-{uuid4().hex[:8]}"
    try:
        shutil.copytree(copy, tmp, ignore=shutil.ignore_patterns(CHECKSUMS))
        if _file_hashes(tmp) != hashes:
            raise BackupIntegrityError(f"{name}: restored files differ from the backup")
        if (tmp / "manifest.json").exists() and (tmp / "data.parquet").exists():
            _validate_dataset(data_dir, tmp, name)
        tmp.rename(target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return target


def _reserved(data_dir: Path, name: str, manifest: dict[str, Any]) -> bool:
    try:
        check_access(data_dir, [(name, manifest)], None)
        return False
    except ReservedDataError:
        return True  # reserved, or reservations unreadable: fail closed


def _validate_dataset(data_dir: Path, path: Path, name: str) -> str:
    """Validate a restored dataset without reading protected outcomes:
    `read_dataset` (whole-file hash against the manifest, schema checks)
    loading only `frame_time`; for a dataset reaching into a reserved
    prospective period, only the bytes are hashed against the manifest and
    the file is not parsed. Returns the scope used."""
    manifest = json.loads((path / "manifest.json").read_text())
    if _reserved(data_dir, name, manifest):
        if file_sha256(path / "data.parquet") != manifest.get("data_sha256"):
            raise BackupIntegrityError(f"{name}: data.parquet doesn't match its manifest")
        return "content_hash_only"
    read_dataset(path, columns=["frame_time"])
    return "full"


# -- recovery test ---------------------------------------------------------------------------


def load_recovery_test(data_dir: Path) -> dict[str, Any] | None:
    try:
        report = json.loads((data_dir / CONTINUITY_DIRNAME / RECOVERY_REPORT).read_text())
        return report if isinstance(report, dict) else None
    except (OSError, ValueError):
        return None


def recovery_test(
    data_dir: Path,
    destination: Path,
    *,
    now: datetime | None = None,
    scratch: Path | None = None,
    max_bytes: int = FULL_RECOVERY_MAX_BYTES,
) -> dict[str, Any]:
    """Restore protected items from the backup into an isolated temporary
    directory (never the data directory), check each restored item's files
    against the backup's checksums *and* the live source's hashes recorded
    by the last backup run, validate restored datasets without reading
    protected outcomes, then delete the temporary directory. Persists and
    returns the result. Non-destructive: nothing outside its own temporary
    directory is written, except the result file."""
    now = now or datetime.now(UTC)
    backup = load_report(data_dir)
    items: dict[str, dict[str, Any]] = backup.get("items", {})
    result: dict[str, Any] = {
        "tested_at": now.isoformat(),
        "destination": str(destination),
        "scope": "full",
        "items_tested": 0,
        "items_protected": len(items),
        "bytes_restored": 0,
        "restricted_items": 0,
        "failures": {},
        "error": None,
    }
    try:
        check_destination(data_dir, destination)
        if backup.get("destination") != str(destination) or not items:
            raise BackupError("no verified backup run exists for this destination yet")
    except BackupError as exc:
        result["error"] = str(exc)
        result["status"] = "unavailable"
        _write_json_atomic(data_dir / CONTINUITY_DIRNAME / RECOVERY_REPORT, result)
        return result

    names = sorted(items)
    sizes = {
        n: sum(
            (destination / n / f).stat().st_size
            for f in items[n].get("files", {})
            if (destination / n / f).exists()
        )
        for n in names
    }
    if sum(sizes.values()) > max_bytes:
        # Representative: every registration and exposure event, then other
        # items in name order until the bound.
        result["scope"] = "representative"
        chosen, total = [], 0
        for n in sorted(
            names, key=lambda n: (not n.startswith(EVALUATIONS_DIRNAME), n), reverse=False
        ):
            if n.startswith(EVALUATIONS_DIRNAME) or total + sizes[n] <= max_bytes:
                chosen.append(n)
                total += sizes[n]
        names = chosen
    root = Path(tempfile.mkdtemp(prefix="market-truffler-recovery-test-", dir=scratch))
    try:
        for name in names:
            target = root / name
            try:
                hashes = verify_copy(destination / name, items[name].get("files"))
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(
                    destination / name, target, ignore=shutil.ignore_patterns(CHECKSUMS)
                )
                if _file_hashes(target) != hashes:
                    raise BackupIntegrityError(f"{name}: restored files differ from the backup")
                if (target / "manifest.json").exists() and (target / "data.parquet").exists():
                    if _validate_dataset(data_dir, target, name) == "content_hash_only":
                        result["restricted_items"] += 1
                result["bytes_restored"] += sizes[name]
            except (BackupIntegrityError, OSError, ValueError) as exc:
                result["failures"][name] = str(exc)
            result["items_tested"] += 1
    finally:
        shutil.rmtree(root, ignore_errors=True)
    result["status"] = "failed" if result["failures"] else "passed"
    _write_json_atomic(data_dir / CONTINUITY_DIRNAME / RECOVERY_REPORT, result)
    return result


def recovery_test_due(data_dir: Path, now: datetime, interval: timedelta) -> bool:
    last = load_recovery_test(data_dir)
    if last is None or last.get("status") != "passed":
        return True
    return now - datetime.fromisoformat(last["tested_at"]) >= interval
