"""Read-only access to OINK CORP's committed research artifacts for the
Research Explorer: campaign files (`<campaigns>/<id>.json` and their
`<id>.report.md`), experiment specs (`<experiments>/<id>.json`) and the
immutable run records (`<data>/runs/<id>/<run_id>/result.json`).

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

import json
import re
from dataclasses import dataclass, field
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
        for base in (self.campaigns_dir, self.experiments_dir, self.data_dir):
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
