"""Read-only access to OINK CORP's committed research artifacts for the
Research Explorer: campaign files (`<campaigns>/<id>.json` and their
`<id>.report.md`), experiment specs (`<experiments>/<id>.json`), knowledge
ledger revisions (`<knowledge>/<campaign_id>/NNNN.json`) and the immutable
run records (`<data>/runs/<id>/<run_id>/result.json`).

Plain JSON and text only — it never imports `app.research` (see
`tests/test_architecture_boundaries.py`), never reads a dataset, never
writes anything. Every identifier from a request is checked against the
slug / run-id patterns the research code itself uses and then resolved
strictly inside its base directory, so no request can reach another path.

Nothing is inferred: verdicts are exactly what the Evaluator (or the
comparison protocol) recorded in each run, and the recorded conclusion is
the campaign report's own text. Artifacts that can't be read are reported
as `issues`, never silently skipped.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{12}Z-[0-9a-f]{6}$")
VERDICTS = ("positive_evidence", "negative_evidence", "inconclusive", "insufficient_data")
_SECTION = re.compile(r"^## +(.+?)\s*$", re.MULTILINE)


class InvalidIdentifier(ValueError):
    pass


@dataclass
class ResearchArtifacts:
    campaigns_dir: Path
    experiments_dir: Path
    data_dir: Path
    knowledge_dir: Path | None = None
    ranking_dir: Path | None = None
    issues: list[dict[str, str]] = field(default_factory=list)

    # -- safe paths --------------------------------------------------------------------------

    def _inside(self, base: Path, *parts: str) -> Path:
        path = base.joinpath(*parts).resolve()
        if base.resolve() not in path.parents:
            raise InvalidIdentifier("path escapes its directory")
        return path

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            self.issues.append({"artifact": self._label(path), "problem": f"unreadable: {exc}"})
            return None
        if not isinstance(value, dict):
            self.issues.append({"artifact": self._label(path), "problem": "not a JSON object"})
            return None
        return value

    def _label(self, path: Path) -> str:
        """A path relative to its artifact directory — never an absolute path."""
        bases = (
            self.campaigns_dir,
            self.experiments_dir,
            self.data_dir,
            self.knowledge_dir,
            self.ranking_dir,
        )
        for base in (b for b in bases if b is not None):
            try:
                return str(path.resolve().relative_to(base.resolve()))
            except ValueError:
                continue
        return path.name

    # -- campaigns and specs ---------------------------------------------------------------------

    def campaign_ids(self) -> list[str]:
        if not self.campaigns_dir.is_dir():
            return []
        return sorted(p.stem for p in self.campaigns_dir.glob("*.json") if SLUG.match(p.stem))

    def campaign(self, campaign_id: str) -> dict[str, Any] | None:
        if not SLUG.match(campaign_id):
            raise InvalidIdentifier(campaign_id)
        path = self._inside(self.campaigns_dir, f"{campaign_id}.json")
        campaign = self._read_json(path)
        if campaign is not None and campaign.get("campaign_id") != campaign_id:
            self.issues.append(
                {"artifact": path.name, "problem": "campaign_id differs from the file name"}
            )
            return None
        return campaign

    def campaigns(self) -> list[dict[str, Any]]:
        return [c for c in (self.campaign(i) for i in self.campaign_ids()) if c is not None]

    def report_sections(self, campaign_id: str) -> list[dict[str, str]] | None:
        """The campaign report split at its `## ` headings, verbatim."""
        if not SLUG.match(campaign_id):
            raise InvalidIdentifier(campaign_id)
        path = self._inside(self.campaigns_dir, f"{campaign_id}.report.md")
        try:
            text = path.read_text()
        except FileNotFoundError:
            return None
        except OSError as exc:
            self.issues.append({"artifact": path.name, "problem": f"unreadable: {exc}"})
            return None
        matches = list(_SECTION.finditer(text))
        sections = [{"title": "", "body": text[: matches[0].start() if matches else len(text)]}]
        for i, match in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            sections.append({"title": match.group(1), "body": text[match.end() : end].strip()})
        return [s for s in sections if s["title"] or s["body"].strip()]

    def experiment_ids(self) -> list[str]:
        if not self.experiments_dir.is_dir():
            return []
        return sorted(p.stem for p in self.experiments_dir.glob("*.json") if SLUG.match(p.stem))

    def spec(self, experiment_id: str) -> dict[str, Any] | None:
        if not SLUG.match(experiment_id):
            raise InvalidIdentifier(experiment_id)
        path = self._inside(self.experiments_dir, f"{experiment_id}.json")
        spec = self._read_json(path)
        if spec is not None and spec.get("experiment_id") != experiment_id:
            self.issues.append(
                {"artifact": path.name, "problem": "experiment_id differs from the file name"}
            )
            return None
        return spec

    # -- run records ---------------------------------------------------------------------------

    def runs(self) -> list[dict[str, Any]]:
        """Every run record, newest first. Hidden (`.`) directories are
        incomplete writes, never records; a record whose ids don't match
        its directory is reported, not trusted."""
        root = self.data_dir / "runs"
        if not root.is_dir():
            return []
        records = []
        for experiment_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            if not SLUG.match(experiment_dir.name):
                continue
            for run_dir in sorted(p for p in experiment_dir.iterdir() if p.is_dir()):
                if not RUN_ID.match(run_dir.name):
                    continue
                record = self._read_json(run_dir / "result.json")
                if record is None:
                    if not (run_dir / "result.json").exists():
                        self.issues.append(
                            {"artifact": self._label(run_dir), "problem": "missing result.json"}
                        )
                    continue
                if (
                    record.get("experiment_id") != experiment_dir.name
                    or record.get("run_id") != run_dir.name
                ):
                    self.issues.append(
                        {
                            "artifact": self._label(run_dir),
                            "problem": "record ids differ from its directory",
                        }
                    )
                    continue
                records.append(record)
        records.sort(key=lambda r: str(r.get("executed_at", "")), reverse=True)
        return records

    def ledger_revisions(self, campaign_id: str) -> list[dict[str, Any]]:
        """A campaign's knowledge ledger revisions, oldest first (written and
        fully verified by `app.scripts.knowledge_ledger`)."""
        if not SLUG.match(campaign_id):
            raise InvalidIdentifier(campaign_id)
        if self.knowledge_dir is None:
            return []
        directory = self._inside(self.knowledge_dir, campaign_id)
        if not directory.is_dir():
            return []
        revisions = []
        for path in sorted(directory.glob("[0-9][0-9][0-9][0-9].json")):
            revision = self._read_json(path)
            if revision is None:
                continue
            if revision.get("campaign_id") != campaign_id or revision.get("revision") != int(
                path.stem
            ):
                self.issues.append(
                    {"artifact": self._label(path), "problem": "revision ids differ from its file"}
                )
                continue
            revisions.append(revision)
        return revisions

    def evaluations(self) -> list[dict[str, Any]]:
        """Prospective evaluation registrations (`<data>/evaluations/`), each
        with its exposure events in sequence order."""
        root = self.data_dir / "evaluations"
        if not root.is_dir():
            return []
        found = []
        for directory in sorted(p for p in root.iterdir() if p.is_dir() and SLUG.match(p.name)):
            stored = self._read_json(directory / "registration" / "registration.json")
            if stored is None or not isinstance(stored.get("registration"), dict):
                if stored is not None:
                    self.issues.append({"artifact": self._label(directory), "problem": "malformed"})
                continue
            registration = stored["registration"]
            if registration.get("registration_id") != directory.name:
                self.issues.append(
                    {"artifact": self._label(directory), "problem": "registration id differs"}
                )
                continue
            events = []
            for path in sorted((directory / "events").glob("[0-9]" * 6 + "/event.json")):
                event = self._read_json(path)
                if event is not None:
                    events.append(event)
            found.append(
                {
                    "registration": registration,
                    "registration_hash": stored.get("registration_hash"),
                    "events": events,
                }
            )
        return found

    def ranking_specs(self, kind: str) -> dict[str, dict[str, Any]]:
        """Ranking `candidates` or `studies`, keyed by their id; a file whose
        id differs from its name is reported, not used."""
        if self.ranking_dir is None or not (self.ranking_dir / kind).is_dir():
            return {}
        key = "candidate_id" if kind == "candidates" else "study_id"
        specs = {}
        for path in sorted((self.ranking_dir / kind).glob("*.json")):
            if not SLUG.match(path.stem):
                continue
            spec = self._read_json(path)
            if spec is None:
                continue
            if spec.get(key) != path.stem:
                self.issues.append({"artifact": self._label(path), "problem": f"{key} differs"})
                continue
            specs[path.stem] = spec
        return specs

    def model_ledgers(self) -> list[dict[str, Any]]:
        """Model knowledge ledgers (`<knowledge>/models/<kind>/<id>/`), each
        `{kind, id, revisions}` oldest first. Written and fully verified by
        `app.scripts.model_knowledge`; here only read, with the hash chain
        re-checked (`chain_problems`)."""
        if self.knowledge_dir is None or not (self.knowledge_dir / "models").is_dir():
            return []
        found = []
        for kind_dir in sorted((self.knowledge_dir / "models").iterdir()):
            if kind_dir.name not in ("ranking", "qualification") or not kind_dir.is_dir():
                continue
            for directory in sorted(
                p for p in kind_dir.iterdir() if p.is_dir() and SLUG.match(p.name)
            ):
                revisions = [
                    r
                    for r in (
                        self._read_json(path)
                        for path in sorted(directory.glob("[0-9][0-9][0-9][0-9].json"))
                    )
                    if r is not None
                ]
                found.append(
                    {
                        "kind": kind_dir.name,
                        "id": directory.name,
                        "revisions": revisions,
                        "chain_problems": chain_problems(revisions),
                    }
                )
        return found

    def run(self, experiment_id: str, run_id: str) -> dict[str, Any] | None:
        if not SLUG.match(experiment_id) or not RUN_ID.match(run_id):
            raise InvalidIdentifier(f"{experiment_id}/{run_id}")
        path = self._inside(self.data_dir / "runs", experiment_id, run_id, "result.json")
        return self._read_json(path)


def verdict_of(record: dict[str, Any]) -> str | None:
    """The recorded verdict, or None for a failed run (no evaluation)."""
    if record.get("status") != "completed":
        return None
    verdict = (record.get("report") or {}).get("verdict")
    return verdict if verdict in VERDICTS else None


def latest_per_segment(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Newest completed-or-failed run per segment (records are newest first)."""
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        latest.setdefault(str(record.get("segment")), record)
    return latest


def check_ledger(revision: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, list[str]]:
    """A light consistency check of a ledger revision against the run
    records it cites (does each run exist with the cited segment and
    verdict?) plus runs of its evidence ids it does not cite yet. The full
    verification — hashes, chain, re-derived statuses — is
    `python -m app.scripts.knowledge_ledger --verify`."""
    by_key = {(r.get("experiment_id"), r.get("run_id")): r for r in records}
    problems: list[str] = []
    uncited: list[str] = []
    for entry in revision.get("hypotheses", []):
        hid = entry.get("hypothesis_id")
        cited = set()
        for reference in entry.get("evidence", []):
            key = (reference.get("experiment_id"), reference.get("run_id"))
            cited.add(key)
            record = by_key.get(key)
            if record is None:
                problems.append(f"{hid}: cited run {key[0]}/{key[1]} does not exist")
            elif reference.get("segment") != record.get("segment") or reference.get("verdict") != (
                record.get("report") or {}
            ).get("verdict"):
                problems.append(f"{hid}: cited run {key[1]} differs from its record")
        evidence_ids = {e.get("experiment_id") for e in entry.get("evidence", [])}
        evidence_ids.update(entry.get("primary_evidence_ids", []))
        evidence_ids.add(entry.get("experiment_id"))
        for record in records:
            key = (record.get("experiment_id"), record.get("run_id"))
            if key[0] in evidence_ids and record.get("status") == "completed" and key not in cited:
                uncited.append(f"{hid}: {key[0]}/{key[1]}")
    return {"problems": problems, "uncited_runs": uncited}


#: Mirrors app.research.prospective.READY_AFTER: a partition with missing
#: shards becomes eligible this long after it ends.
READY_AFTER_DAYS = 2


def _attempt_outcome(types: list[str]) -> str:
    if "completed" in types:
        return "completed"
    if "failed" in types:
        return "failed" if "started" in types else "failed_before_exposure"
    if "started" in types:
        return "exposure_uncertain"
    return "authorized_only"


def evaluation_states(
    registration: dict[str, Any], events: list[dict[str, Any]], data_dir: Path, now: datetime
) -> list[dict[str, Any]]:
    """Each partition's state from the exposure log, the partition's dates
    and which daily shards exist — the same rules as
    `app.research.prospective.partition_state`, read-only."""
    states = []
    for partition in registration.get("partitions", []):
        role = partition.get("role")
        start, end = date.fromisoformat(partition["start"]), date.fromisoformat(partition["end"])
        days = [start + timedelta(days=i) for i in range((end - start).days)]
        present = sum(
            1 for d in days if (data_dir / f"shard_s15m_{d:%Y%m%d}" / "manifest.json").exists()
        )
        end_at = datetime.combine(end, datetime.min.time(), tzinfo=UTC)
        eligible_from = (
            end_at if present == len(days) else end_at + timedelta(days=READY_AFTER_DAYS)
        )
        by_attempt: dict[str, list[str]] = {}
        for e in events:
            if e.get("role") == role:
                by_attempt.setdefault(str(e.get("attempt_id")), []).append(str(e.get("type")))
        outcomes = [_attempt_outcome(types) for types in by_attempt.values()]
        if outcomes:
            state = outcomes[-1]
        elif now >= eligible_from:
            state = "eligible"
        else:
            state = "awaiting_data"
        states.append(
            {
                "role": role,
                "start": partition["start"],
                "end": partition["end"],
                "state": state,
                "eligible_from": eligible_from.isoformat(),
                "days": len(days),
                "days_with_shard": present,
                "attempts": len(outcomes),
                "exposed": any(
                    o not in ("authorized_only", "failed_before_exposure") for o in outcomes
                ),
            }
        )
    return states


def _content_hash(revision: dict[str, Any]) -> str:
    """Mirrors `app.research.knowledge.content_hash` (read-only check)."""
    body = {k: v for k, v in revision.items() if k != "content_hash"}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def chain_problems(revisions: list[dict[str, Any]]) -> list[str]:
    """Content hashes and the previous-revision chain of one ledger — the
    integrity part of verification; evidence and status are re-derived only
    by `app.scripts.model_knowledge --verify`."""
    problems = []
    previous = None
    for number, revision in enumerate(revisions, start=1):
        if revision.get("revision") != number:
            problems.append(f"revision {revision.get('revision')} out of sequence")
        if revision.get("content_hash") != _content_hash(revision):
            problems.append(f"revision {number}: content hash does not match")
        if revision.get("previous_revision_hash") != previous:
            problems.append(f"revision {number}: breaks the revision chain")
        previous = revision.get("content_hash")
    return problems
