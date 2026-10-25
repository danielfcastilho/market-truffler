"""OINK CORP model knowledge: what is known about each ranking model and
each Truffle qualification policy, and which immutable evidence supports
it.

    ranking model ─┬─> qualification policy ─> Truffle registration ─> evidence
                   └─> ranking studies (exploratory)

One ledger per identity, `research/oink_corp/knowledge/models/<kind>/<id>/
NNNN.json`, written with the campaign ledger's own revision machinery
(`app.research.knowledge.append_revision`: numbered, hash-chained, never
overwritten, written only when the derived content changes). Nothing here
is authored: a revision is *derived* from the spec file and the artifacts
that reference its exact hash, and `verify_model_revision` re-derives the
status from exactly the evidence a revision cites — so a favourable status
cannot be written in without the evidence for it.

IDENTITY. A ranking model and a qualification policy are distinct
identities (`kind` "ranking_model" / "qualification_policy"), each fixed
by id *and* spec hash at its first revision. A changed spec file under the
same id is refused: a changed specification needs a new id, and the old
ledger keeps describing the old hash.

EVIDENCE CLASSES, never interchangeable:
- `human_hypothesis` — the spec's own stated objective/description (what a
  person asserted, not evidence);
- `exploratory_development` — ranking-study runs on development data;
- `prospective_registration` — a registration that froze this exact hash;
- `prospective_validation` / `protected_holdout` — an *independent*
  completed Truffle evaluation (verified against its exposure log);
- `prospective_reused` — a completed evaluation that was not independent;
- `operational_observation` — reserved; none are recorded yet.

STATUS (MODEL_KNOWLEDGE_RULES_VERSION 1), weakest justified:
- experimental — the default; live use, Truffles, development results or
  number of experiments never change it;
- registered_for_prospective_evaluation — a registration froze this hash
  and no independent evaluation of it has completed;
- insufficient_prospective_evidence / inconclusive_prospective_evidence —
  the independent evaluations completed with that decision;
- supported_by_prospective_evidence / contradicted_by_prospective_evidence
  — every independent decision says so;
- conflicting_prospective_evidence — independent decisions disagree.

A Truffle evaluation evaluates a *qualification policy*: it moves that
policy's status only. The ranking model it applies to links to the
evaluation but stays experimental / registered, because no evaluation of
the ranking itself exists. Nothing is ever promoted automatically, and
nothing here is a Champion.
"""

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from app.ranking.qualification import (
    QUALIFICATION_EVALUATOR_VERSION,
    load_qualification,
    qualification_hash,
)
from app.ranking.scoring import candidate_hash, load_candidate_file
from app.research.knowledge import (
    LEDGER_SCHEMA_VERSION,
    LedgerError,
    append_revision,
    content_hash,
    load_revisions_in,
)
from app.research.reservations import read_events, read_registration_file, registration_dirs
from app.research.runs import RUNS_DIRNAME, load_runs
from app.research.truffle_evaluation import TRUFFLE_KIND, verify_truffle_run

MODEL_KNOWLEDGE_RULES_VERSION = 1
MODELS_DIRNAME = "models"
Kind = Literal["ranking_model", "qualification_policy"]
_KIND_DIR = {"ranking_model": "ranking", "qualification_policy": "qualification"}
SCOPE_NOTE = (
    "Model-level knowledge: what was registered and observed about this exact specification "
    "hash. A status describes evidence, not a recommendation; nothing here is a trading signal "
    "or a Champion."
)
_DECISION_STATUS = {
    "consistent_with_improvement": "supported_by_prospective_evidence",
    "inconsistent_with_improvement": "contradicted_by_prospective_evidence",
    "insufficient_evidence": "insufficient_prospective_evidence",
    "inconclusive": "inconclusive_prospective_evidence",
}
_INDEPENDENT = ("prospective_validation", "protected_holdout")


# -- identities ------------------------------------------------------------------------------


def ranking_identity(candidates_dir: Path, candidate_id: str) -> dict[str, Any]:
    candidate = load_candidate_file(candidates_dir / f"{candidate_id}.json")
    return {
        "kind": "ranking_model",
        "id": candidate.candidate_id,
        "spec_version": candidate.spec_version,
        "spec_hash": candidate_hash(candidate),
        "model_family": candidate.model_family,
        "direction": candidate.direction,
        "features": candidate.features,
        "transforms": {t.feature: t.transform for t in candidate.terms},
        "parameters": {
            "weights": {t.feature: t.weight for t in candidate.terms},
            "missing_policy": candidate.missing_policy,
        },
        "source": f"ranking/candidates/{candidate_id}.json",
    }


def qualification_identity(qualifications_dir: Path, qualification_id: str) -> dict[str, Any]:
    spec = load_qualification(qualifications_dir, qualification_id)
    return {
        "kind": "qualification_policy",
        "id": spec.qualification_id,
        "spec_version": spec.spec_version,
        "spec_hash": qualification_hash(spec),
        "applies_to": {"ranking_model": spec.ranking_candidate, "direction": spec.direction},
        "direction": spec.direction,
        "policy": spec.policy,
        "features": spec.features,
        "parameters": {
            "conditions": [
                {k: v for k, v in c.model_dump().items() if k != "rationale"}
                for c in spec.conditions
            ],
            "min_frame_completeness": spec.min_frame_completeness,
            "qualification_evaluator_version": QUALIFICATION_EVALUATOR_VERSION,
        },
        "source": f"ranking/qualifications/{qualification_id}.json",
    }


# -- evidence ------------------------------------------------------------------------------


def _dataset_contract(data_dir: Path, name: str) -> int | None:
    path = data_dir / name / "manifest.json"
    if not path.exists():
        return None
    return int(json.loads(path.read_text()).get("temporal_contract_version") or 1)


def collect_evidence(
    identity: dict[str, Any],
    data_dir: Path,
    *,
    human_text: str,
    related_qualifications: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Every artifact that references this identity's exact hash, as
    deterministic, sorted references. `related_qualifications` (for a
    ranking model) links the evaluations of the policies applied to it."""
    spec_hash = identity["spec_hash"]
    evidence: list[dict[str, Any]] = [
        {"class": "human_hypothesis", "ref": identity["source"], "text": human_text}
    ]
    for record in load_runs(data_dir / RUNS_DIRNAME).records:
        if record.get("kind") != "ranking_study" or record.get("status") != "completed":
            continue
        roles = [
            role
            for role in ("candidate", "reference")
            if (record.get(role) or {}).get("spec_hash") == spec_hash
        ]
        if identity["kind"] != "ranking_model" or not roles:
            continue
        contracts = sorted(
            {_dataset_contract(data_dir, d["name"]) or 1 for d in record["datasets"]}
        )
        evidence.append(
            {
                "class": "exploratory_development",
                "ref": f"{record['experiment_id']}/{record['run_id']}",
                "role": roles[0],
                "segment": record.get("segment"),
                "verdict": (record.get("report") or {}).get("verdict"),
                "datasets": sorted(d["name"] for d in record["datasets"]),
                "temporal_contract_versions": contracts,
            }
        )
    hash_field = (
        "ranking_spec_hash" if identity["kind"] == "ranking_model" else ("qualification_spec_hash")
    )
    for directory in registration_dirs(data_dir):
        stored = read_registration_file(directory)
        registration = stored["registration"]
        if registration.get("kind") != TRUFFLE_KIND:
            continue
        for claim in registration.get("claims", []):
            if claim.get(hash_field) != spec_hash:
                continue
            evidence.append(
                {
                    "class": "prospective_registration",
                    "ref": registration["registration_id"],
                    "registration_hash": stored.get("registration_hash"),
                    "hypothesis_id": claim["hypothesis_id"],
                    "partitions": registration["partitions"],
                    "exposure_events": len(read_events(directory)),
                }
            )
    for record in load_runs(data_dir / RUNS_DIRNAME).records:
        if record.get("kind") != "prospective_truffle_evaluation":
            continue
        if record.get(hash_field) != spec_hash:
            continue
        evidence.append(_prospective_entry(record))
    for related in related_qualifications or []:
        for entry in related["evidence"]:
            if entry["class"] in ("prospective_registration", *_INDEPENDENT, "prospective_reused"):
                evidence.append({**entry, "via_qualification_policy": related["identity"]["id"]})
    return sorted(evidence, key=lambda e: (e["class"], e["ref"], e.get("hypothesis_id", "")))


def _prospective_entry(record: dict[str, Any]) -> dict[str, Any]:
    independent = record.get("independence") == "independent_prospective"
    cls = (
        ("protected_holdout" if record.get("role") == "holdout" else "prospective_validation")
        if independent
        else "prospective_reused"
    )
    return {
        "class": cls,
        "ref": f"{record['experiment_id']}/{record['run_id']}",
        "registration_id": record.get("registration_id"),
        "hypothesis_id": record.get("hypothesis_id"),
        "role": record.get("role"),
        "independence": record.get("independence"),
        "decision": record.get("decision"),
    }


# -- status --------------------------------------------------------------------------------


def derive_status(kind: Kind, evidence: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """(status, the evidence refs it rests on), weakest state justified."""
    own = [e for e in evidence if "via_qualification_policy" not in e]
    registered = [e["ref"] for e in evidence if e["class"] == "prospective_registration"]
    if kind == "qualification_policy":
        independent = [e for e in own if e["class"] in _INDEPENDENT]
        if independent:
            statuses = {_DECISION_STATUS[str(e["decision"])] for e in independent}
            refs = sorted(e["ref"] for e in independent)
            directional = statuses & {
                "supported_by_prospective_evidence",
                "contradicted_by_prospective_evidence",
            }
            if len(directional) == 2:
                return "conflicting_prospective_evidence", refs
            if len(statuses) == 1:
                return statuses.pop(), refs
            # e.g. one inconclusive and one supported: the weakest is inconclusive
            for weakest in (
                "insufficient_prospective_evidence",
                "inconclusive_prospective_evidence",
            ):
                if weakest in statuses:
                    return weakest, refs
    if registered:
        return "registered_for_prospective_evaluation", sorted(set(registered))
    return "experimental", []


def _limitations(identity: dict[str, Any], evidence: list[dict[str, Any]]) -> list[str]:
    notes = []
    old = [
        e["ref"]
        for e in evidence
        if e["class"] == "exploratory_development" and 1 in e.get("temporal_contract_versions", [])
    ]
    if old:
        notes.append(
            "Exploratory runs on datasets produced under temporal contract v1 (Open Interest "
            "availability unverified) are not directly comparable with v2 data: " + ", ".join(old)
        )
    if not any(e["class"] in _INDEPENDENT for e in evidence):
        notes.append("No independent prospective evidence exists for this specification.")
    if identity["kind"] == "ranking_model":
        notes.append(
            "Truffle evaluations evaluate qualification policies applied to this model; they "
            "do not evaluate the ranking itself and never change its status."
        )
    notes.append("No operational observations are recorded yet.")
    return notes


def derive_model_knowledge(
    identity: dict[str, Any], evidence: list[dict[str, Any]], links: dict[str, Any]
) -> dict[str, Any]:
    status, basis = derive_status(identity["kind"], evidence)
    return {
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "ledger": "model",
        "model_knowledge_rules_version": MODEL_KNOWLEDGE_RULES_VERSION,
        "scope_note": SCOPE_NOTE,
        "identity": identity,
        "status": status,
        "status_basis": basis,
        "evidence_counts": dict(sorted(Counter(e["class"] for e in evidence).items())),
        "evidence": evidence,
        "links": links,
        "limitations": _limitations(identity, evidence),
    }


# -- the configured identities -------------------------------------------------------------


def derive_all(
    ranking_dir: Path, data_dir: Path, pairs: list[tuple[str, str]]
) -> list[dict[str, Any]]:
    """Model knowledge for every configured (ranking model, qualification
    policy) pair — the live ones. Qualification policies first, so a
    ranking model can link their evaluations."""
    candidates, qualifications = ranking_dir / "candidates", ranking_dir / "qualifications"
    derived = []
    by_model: dict[str, list[dict[str, Any]]] = {}
    for candidate_id, qualification_id in pairs:
        identity = qualification_identity(qualifications, qualification_id)
        spec = load_qualification(qualifications, qualification_id)
        evidence = collect_evidence(identity, data_dir, human_text=spec.description)
        item = derive_model_knowledge(
            identity, evidence, {"ranking_model": identity["applies_to"]["ranking_model"]}
        )
        derived.append(item)
        by_model.setdefault(candidate_id, []).append(item)
    for candidate_id in dict.fromkeys(c for c, _ in pairs):
        identity = ranking_identity(candidates, candidate_id)
        candidate = load_candidate_file(candidates / f"{candidate_id}.json")
        related = by_model.get(candidate_id, [])
        evidence = collect_evidence(
            identity,
            data_dir,
            human_text=f"{candidate.name}: {candidate.objective}",
            related_qualifications=related,
        )
        derived.append(
            derive_model_knowledge(
                identity,
                evidence,
                {"qualification_policies": sorted(r["identity"]["id"] for r in related)},
            )
        )
    return derived


def ledger_path(knowledge_root: Path, identity: dict[str, Any]) -> Path:
    return knowledge_root / MODELS_DIRNAME / _KIND_DIR[identity["kind"]] / identity["id"]


def sync(
    knowledge_root: Path, derived: list[dict[str, Any]], *, now: datetime | None = None
) -> list[tuple[str, Path | None]]:
    """Append a revision per identity whose derived knowledge changed —
    idempotent. Refuses a changed spec hash under an existing identity."""
    written = []
    for item in derived:
        identity = item["identity"]
        directory = ledger_path(knowledge_root, identity)
        revisions = load_revisions_in(directory)
        if revisions and revisions[0]["identity"]["spec_hash"] != identity["spec_hash"]:
            raise LedgerError(
                f"{identity['kind']} {identity['id']}: its specification changed (hash "
                f"{revisions[0]['identity']['spec_hash'][:12]}… -> {identity['spec_hash'][:12]}…); "
                "a changed specification needs a new id — the old ledger is never rewritten"
            )
        path, _ = append_revision(directory, item, now=now)
        written.append((f"{identity['kind']}/{identity['id']}", path))
    return written


# -- verification --------------------------------------------------------------------------


def verify_model_revision(
    revision: dict[str, Any],
    previous: dict[str, Any] | None,
    current_identity: dict[str, Any],
    data_dir: Path,
) -> list[str]:
    """Every reason a revision is not supported (empty = verified): hash
    chain, identity against the spec file, every cited evidence entry
    against the artifact it names (prospective runs against their exposure
    logs), and the status re-derived from exactly that evidence."""
    problems = []
    if revision.get("ledger") != "model":
        return ["not a model knowledge revision"]
    if revision.get("model_knowledge_rules_version") != MODEL_KNOWLEDGE_RULES_VERSION:
        return [f"unknown rules version {revision.get('model_knowledge_rules_version')!r}"]
    if revision.get("content_hash") != content_hash(revision):
        problems.append("content_hash does not match the revision's content")
    if revision.get("previous_revision_hash") != (previous["content_hash"] if previous else None):
        problems.append("previous_revision_hash breaks the revision chain")
    if revision.get("identity") != current_identity:
        problems.append("identity differs from its specification file (id or hash changed)")
    evidence = revision.get("evidence", [])
    runs = {
        f"{r.get('experiment_id')}/{r.get('run_id')}": r
        for r in load_runs(data_dir / RUNS_DIRNAME).records
    }
    for entry in evidence:
        cls = entry.get("class")
        if cls in (*_INDEPENDENT, "prospective_reused"):
            record = runs.get(entry.get("ref"))
            if record is None:
                problems.append(f"{entry.get('ref')}: cited run does not exist")
                continue
            problems += verify_truffle_run(data_dir, record)
            expected = _prospective_entry(record)
            if {k: entry.get(k) for k in expected} != expected:
                problems.append(f"{entry.get('ref')}: entry differs from the run record")
        elif cls == "exploratory_development":
            record = runs.get(entry.get("ref"))
            if record is None or (record.get("report") or {}).get("verdict") != entry.get(
                "verdict"
            ):
                problems.append(f"{entry.get('ref')}: cited run missing or verdict differs")
        elif cls == "prospective_registration":
            try:
                stored = read_registration_file(data_dir / "evaluations" / str(entry.get("ref")))
            except ValueError as exc:
                problems.append(f"{entry.get('ref')}: {exc}")
                continue
            if stored.get("registration_hash") != entry.get("registration_hash"):
                problems.append(f"{entry.get('ref')}: registration hash differs")
        elif cls not in ("human_hypothesis", "operational_observation"):
            problems.append(f"unknown evidence class {cls!r}")
    status, basis = derive_status(current_identity["kind"], evidence)
    if (revision.get("status"), revision.get("status_basis")) != (status, basis):
        problems.append(
            f"status {revision.get('status')!r} is not what its cited evidence supports "
            f"({status!r})"
        )
    return problems


def verify_ledger(
    knowledge_root: Path, identity: dict[str, Any], data_dir: Path
) -> list[tuple[int, list[str]]]:
    revisions = load_revisions_in(ledger_path(knowledge_root, identity))
    out = []
    previous = None
    for revision in revisions:
        out.append(
            (
                revision.get("revision", 0),
                verify_model_revision(revision, previous, identity, data_dir),
            )
        )
        previous = revision
    return out


def identities(ranking_dir: Path, pairs: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """The current identities of the configured pairs (raises
    `LedgerError` on an unreadable spec)."""
    try:
        found = [qualification_identity(ranking_dir / "qualifications", q) for _, q in pairs]
        found += [
            ranking_identity(ranking_dir / "candidates", c)
            for c in dict.fromkeys(c for c, _ in pairs)
        ]
    except (OSError, ValueError, ValidationError) as exc:
        raise LedgerError(str(exc)) from exc
    return found
