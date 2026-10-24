"""OINK CORP knowledge ledger: what a campaign's evidence currently
justifies about each of its hypotheses, derived deterministically from the
campaign's pre-registration and its immutable run records.

    hypothesis (campaign) -> experiment / comparison -> immutable run
        -> verdict (Evaluator / comparison protocol) -> knowledge (here)

The ledger never evaluates anything and never takes a verdict from anywhere
but a run record. It is a set of immutable, hash-chained JSON revisions,
`research/oink_corp/knowledge/<campaign_id>/<NNNN>.json`: `derive_ledger`
builds the current state, `write_revision` stores it only if it differs from
the latest revision, and `verify_revision` re-derives a revision from the
runs it cites and rejects it unless every reference, verdict, hash and
status matches — so no process (or person) can write "supported" into a
ledger without the immutable evidence for it.

EVIDENCE. A hypothesis' *primary* evidence is the campaign's comparison(s)
registered for it; a hypothesis without a comparison is judged on its own
experiment. Per evidence id and segment only the latest completed run is
*current*: repeated runs of the same spec read the same hash-pinned
datasets, so they are repeats, never independent replications (they're
listed, flagged `current: false`, and must agree with the current verdict).
The hypothesis' own experiment-vs-universe runs are listed as context and
never decide its status.

PREDICTION SIGN. Campaign v1 registers predictions as text. Exactly these
forms are recognized: text starting "Positive" -> a positive spread is
predicted, "Negative" -> negative, containing "No direction assumed" ->
no sign. Anything else is "unspecified" and treated like no sign: evidence
in either direction can then only show *a difference*, never support.

STATUS (KNOWLEDGE_RULES_VERSION 1), weakest justified state:

- untested — no completed primary run;
- insufficient_evidence / inconclusive — the current development verdict
  (or, without one, the validation verdict) is insufficient_data /
  inconclusive;
- preliminary_support / preliminary_contradiction — a directional verdict
  agreeing / disagreeing with a signed prediction, without independent
  validation agreeing;
- preliminary_difference — a directional verdict for an unsigned prediction;
- independently_supported / independently_contradicted /
  independently_observed_difference — development and an *independent*
  validation run (validation_reuse.independent) both directional with the
  same sign;
- conflicting_evidence — development and independent validation
  directional with opposite signs.

A validation run that reused earlier validation days never upgrades a
status. All of this is feature-level knowledge ("this condition separated
outcomes in this sample"); nothing here says a ranking model would improve.
"""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from app.research.campaign import Campaign, campaign_hash
from app.research.experiment import ExperimentSpec

LEDGER_SCHEMA_VERSION = 1
#: Rules v2 = v1 plus prospective evaluations (`app.research.prospective`).
#: Revisions written under v1 are still verified with the v1 rules.
KNOWLEDGE_RULES_VERSION = 2
SUPPORTED_RULES = (1, 2)
#: Evidence classes (rules v2) that can establish independent knowledge.
INDEPENDENT_CLASSES = ("prospective_validation", "protected_holdout")
KNOWLEDGE_DIRNAME = "knowledge"

PredictionSign = Literal["positive", "negative", "none", "unspecified"]
KNOWLEDGE_STATUSES = (
    "untested",
    "insufficient_evidence",
    "inconclusive",
    "preliminary_support",
    "preliminary_contradiction",
    "preliminary_difference",
    "independently_supported",
    "independently_contradicted",
    "independently_observed_difference",
    "conflicting_evidence",
)
SCOPE_NOTE = (
    "Feature-level knowledge only: whether a registered market condition separated "
    "subsequent outcomes in these samples. It is not evidence that any ranking model "
    "using these features would perform better."
)
_DIRECTIONAL = {"positive_evidence": 1, "negative_evidence": -1}


class LedgerError(ValueError):
    """A ledger revision that its evidence does not support."""


def prediction_sign(prediction: str) -> PredictionSign:
    text = prediction.strip()
    if text.startswith("Positive"):
        return "positive"
    if text.startswith("Negative"):
        return "negative"
    if "No direction assumed" in text:
        return "none"
    return "unspecified"


def classify(
    sign: PredictionSign,
    development: dict[str, Any] | None,
    validation: dict[str, Any] | None,
) -> tuple[str, str]:
    """(knowledge_status, validation_status) from the current development
    and validation evidence entries (each `{"verdict", "independent"}`)."""
    dev = development["verdict"] if development else None
    val = validation["verdict"] if validation else None
    if validation is None:
        validation_status = "not_validated"
    elif not validation.get("independent"):
        validation_status = "validation_not_independent"
    else:
        validation_status = "independently_validated"

    expected = {"positive": 1, "negative": -1}.get(sign)

    def preliminary(verdict: str) -> str:
        observed = _DIRECTIONAL[verdict]
        if expected is None:
            return "preliminary_difference"
        return "preliminary_support" if observed == expected else "preliminary_contradiction"

    if dev is None and val is None:
        return "untested", validation_status
    if (
        validation_status == "independently_validated"
        and dev in _DIRECTIONAL
        and val in _DIRECTIONAL
    ):
        if _DIRECTIONAL[dev] != _DIRECTIONAL[val]:
            return "conflicting_evidence", validation_status
        if expected is None:
            return "independently_observed_difference", validation_status
        agrees = _DIRECTIONAL[val] == expected
        return (
            "independently_supported" if agrees else "independently_contradicted",
            validation_status,
        )
    basis = dev if dev is not None else val
    if basis in _DIRECTIONAL:
        return preliminary(basis), validation_status
    return (
        "insufficient_evidence" if basis == "insufficient_data" else "inconclusive",
        validation_status,
    )


# -- evidence -------------------------------------------------------------------------------


def _protocol(record: dict[str, Any]) -> str:
    if record.get("kind") == "incremental_comparison":
        return f"comparison-v{record.get('comparison_version')}"
    return f"evaluator-v{record.get('evaluator_version')}"


def evidence_class(record: dict[str, Any]) -> str:
    """What kind of evidence a run is (rules v2): development,
    retrospective validation, prospective validation, protected holdout, or
    reused / independence_undetermined when the protocol could not establish
    independence."""
    if record.get("kind") == "prospective_evaluation":
        independence = record.get("independence")
        if independence == "independent_prospective":
            return (
                "protected_holdout" if record.get("role") == "holdout" else "prospective_validation"
            )
        if independence in ("repeat_exposure", "claim_previously_exposed"):
            return "reused"
        return "independence_undetermined"
    if record.get("segment") == "validation":
        independent = ((record.get("report") or {}).get("validation_reuse") or {}).get(
            "independent"
        )
        return "retrospective_validation" if independent else "reused"
    return "development"


def _reference(record: dict[str, Any], role: str, rules_version: int = 1) -> dict[str, Any]:
    report = record.get("report") or {}
    extra: dict[str, Any] = {}
    if rules_version >= 2:
        extra["evidence_class"] = evidence_class(record)
        if record.get("kind") == "prospective_evaluation":
            extra["registration_id"] = record.get("registration_id")
            extra["independence"] = record.get("independence")
    return {
        **extra,
        "experiment_id": record["experiment_id"],
        "run_id": record["run_id"],
        "role": role,
        "segment": record.get("segment"),
        "verdict": report.get("verdict"),
        "independent": (report.get("validation_reuse") or {}).get("independent"),
        "protocol": _protocol(record),
        "spec_hash": record.get("spec_hash"),
        "dataset_sha256": sorted(d["data_sha256"] for d in record.get("datasets", [])),
        "executed_at": record.get("executed_at"),
    }


def _evidence_ids(campaign: Campaign, hypothesis_id: str) -> tuple[list[str], str]:
    hypothesis = next(h for h in campaign.hypotheses if h.id == hypothesis_id)
    comparisons = [c.comparison_id for c in campaign.comparisons if c.hypothesis == hypothesis_id]
    return comparisons or [hypothesis.experiment_id], hypothesis.experiment_id


def collect_evidence(
    campaign: Campaign,
    hypothesis_id: str,
    records: list[dict[str, Any]],
    rules_version: int = KNOWLEDGE_RULES_VERSION,
) -> list[dict[str, Any]]:
    """References to every completed run bearing on the hypothesis, newest
    first, each marked `current` (the latest per evidence id and segment).
    Rules v2 also count the campaign's prospective evaluations of it."""
    primary, own = _evidence_ids(campaign, hypothesis_id)
    roles = {i: "primary" for i in primary}
    roles.setdefault(own, "context")
    if rules_version >= 2:
        for r in records:
            if (
                r.get("kind") == "prospective_evaluation"
                and r.get("campaign_id") == campaign.campaign_id
                and r.get("hypothesis_id") == hypothesis_id
            ):
                roles[r["experiment_id"]] = "primary"
    relevant = [
        r for r in records if r.get("experiment_id") in roles and r.get("status") == "completed"
    ]
    relevant.sort(key=lambda r: (str(r.get("executed_at")), r["run_id"]), reverse=True)
    seen: set[tuple[str, str]] = set()
    evidence = []
    for record in relevant:
        reference = _reference(record, roles[record["experiment_id"]], rules_version)
        key = (record["experiment_id"], str(record.get("segment")))
        reference["current"] = key not in seen
        seen.add(key)
        evidence.append(reference)
    return evidence


def _is_prospective(entry: dict[str, Any]) -> bool:
    return "registration_id" in entry


def _current(evidence: list[dict[str, Any]], segment: str) -> dict[str, Any] | None:
    """The current retrospective primary evidence for a segment. With
    several primary comparisons the weakest verdict decides (a hypothesis is
    only as established as its least established registered test)."""
    current = [
        e
        for e in evidence
        if e["role"] == "primary"
        and e["current"]
        and e["segment"] == segment
        and not _is_prospective(e)
    ]
    if not current:
        return None
    verdicts = {e["verdict"] for e in current}
    if len(verdicts) > 1:
        weakest = "insufficient_data" if "insufficient_data" in verdicts else "inconclusive"
        independent = all(e.get("independent") for e in current)
        return {"verdict": weakest, "independent": independent}
    return current[0]


def classify_v2(sign: PredictionSign, evidence: list[dict[str, Any]]) -> tuple[str, str]:
    """Rules v2: only current prospective evidence classed
    prospective_validation or protected_holdout can make knowledge
    independent; everything else falls back to the v1 rules, whatever its
    segment label says."""
    prospective = [
        e for e in evidence if e["role"] == "primary" and e["current"] and _is_prospective(e)
    ]
    independent = [e for e in prospective if e.get("evidence_class") in INDEPENDENT_CLASSES]
    knowledge, validation = classify(
        sign, _current(evidence, "development"), _current(evidence, "validation")
    )
    if prospective:
        validation = (
            "prospectively_validated" if independent else "prospective_evidence_not_independent"
        )
    signs = {_DIRECTIONAL[e["verdict"]] for e in independent if e["verdict"] in _DIRECTIONAL}
    if not signs:
        return knowledge, validation
    development = _current(evidence, "development")
    dev_sign = _DIRECTIONAL.get(development["verdict"]) if development else None
    if len(signs) > 1 or (dev_sign is not None and dev_sign not in signs):
        return "conflicting_evidence", validation
    expected = {"positive": 1, "negative": -1}.get(sign)
    if expected is None:
        return "independently_observed_difference", validation
    observed = next(iter(signs))
    return (
        "independently_supported" if observed == expected else "independently_contradicted",
        validation,
    )


def _limitations(
    sign: PredictionSign,
    evidence: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
    rules_version: int = 1,
) -> list[str]:
    notes: list[str] = []
    for e in evidence:
        if e["role"] == "primary" and e["current"]:
            reasons = (records[e["run_id"]].get("report") or {}).get("verdict_reasons", [])
            notes.extend(f"{e['segment']}: {reason}" for reason in reasons)
    segments = {e["segment"] for e in evidence if e["role"] == "primary"}
    if "validation" not in segments:
        notes.append("No validation run: nothing is independently confirmed.")
    elif any(
        e["role"] == "primary" and e["segment"] == "validation" and not e.get("independent")
        for e in evidence
        if e["current"]
    ):
        notes.append("Validation reused days already used by earlier validation runs.")
    if rules_version >= 2:
        prospective = [e for e in evidence if e["current"] and _is_prospective(e)]
        if not prospective:
            notes.append(
                "No prospective evaluation yet: no evidence comes from data that was unseen when "
                "the claim was frozen."
            )
        for e in prospective:
            if e.get("evidence_class") not in INDEPENDENT_CLASSES:
                notes.append(
                    f"Prospective {e['segment']} run {e['run_id']} is not independent "
                    f"({e.get('independence')})."
                )
    if sign in ("none", "unspecified"):
        notes.append(
            "The prediction registered no direction: a directional verdict can only show "
            "that outcomes differed, not support a claim."
        )
    return notes


def derive_hypothesis(
    campaign: Campaign,
    specs: dict[str, ExperimentSpec],
    hypothesis_id: str,
    records: list[dict[str, Any]],
    rules_version: int = KNOWLEDGE_RULES_VERSION,
) -> dict[str, Any]:
    hypothesis = next(h for h in campaign.hypotheses if h.id == hypothesis_id)
    spec = specs[hypothesis.experiment_id]
    sign = prediction_sign(hypothesis.prediction)
    evidence = collect_evidence(campaign, hypothesis_id, records, rules_version)
    if rules_version >= 2:
        knowledge, validation = classify_v2(sign, evidence)
    else:
        knowledge, validation = classify(
            sign, _current(evidence, "development"), _current(evidence, "validation")
        )
    by_run = {r["run_id"]: r for r in records}
    primary, _ = _evidence_ids(campaign, hypothesis_id)
    return {
        "hypothesis_id": hypothesis.id,
        "statement": hypothesis.statement,
        "prediction": hypothesis.prediction,
        "prediction_sign": sign,
        "direction": spec.hypothesis.direction,
        "experiment_id": hypothesis.experiment_id,
        "primary_evidence_ids": primary,
        "knowledge_status": knowledge,
        "validation_status": validation,
        "evidence": evidence,
        "limitations": _limitations(sign, evidence, by_run, rules_version),
    }


def repeat_conflicts(hypothesis_id: str, evidence: list[dict[str, Any]]) -> list[str]:
    """Repeated runs of one evidence id, segment and protocol read the same
    hash-pinned data with the same rules, so they must agree; if they don't,
    something other than the evidence changed and no status is justified."""
    current = {
        (e["experiment_id"], e["segment"], e["protocol"]): e for e in evidence if e["current"]
    }
    problems = []
    for e in evidence:
        if e["current"]:
            continue
        latest = current.get((e["experiment_id"], e["segment"], e["protocol"]))
        if latest is not None and latest["verdict"] != e["verdict"]:
            problems.append(
                f"{hypothesis_id}: repeated runs of {e['experiment_id']} ({e['segment']}, "
                f"{e['protocol']}) disagree: {e['verdict']} vs {latest['verdict']}"
            )
    return problems


def derive_ledger(
    campaign: Campaign,
    specs: dict[str, ExperimentSpec],
    records: list[dict[str, Any]],
    rules_version: int = KNOWLEDGE_RULES_VERSION,
) -> dict[str, Any]:
    """The knowledge content justified by `records` now (no revision
    fields). Refuses (`LedgerError`) when repeated runs disagree."""
    hypotheses = [
        derive_hypothesis(campaign, specs, h.id, records, rules_version)
        for h in campaign.hypotheses
    ]
    conflicts = [c for h in hypotheses for c in repeat_conflicts(h["hypothesis_id"], h["evidence"])]
    if conflicts:
        raise LedgerError("; ".join(conflicts))
    return {
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "knowledge_rules_version": rules_version,
        "campaign_id": campaign.campaign_id,
        "campaign_hash": campaign_hash(campaign, specs),
        "scope": "feature_level",
        "scope_note": SCOPE_NOTE,
        "hypotheses": hypotheses,
    }


# -- revisions -----------------------------------------------------------------------------


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(revision: dict[str, Any]) -> str:
    body = {k: v for k, v in revision.items() if k != "content_hash"}
    return hashlib.sha256(_canonical(body).encode()).hexdigest()


def _knowledge_part(revision: dict[str, Any]) -> dict[str, Any]:
    revision_fields = ("revision", "previous_revision_hash", "derived_at", "content_hash")
    return {k: v for k, v in revision.items() if k not in revision_fields}


def ledger_dir(knowledge_root: Path, campaign_id: str) -> Path:
    return knowledge_root / campaign_id


def load_revisions(knowledge_root: Path, campaign_id: str) -> list[dict[str, Any]]:
    directory = ledger_dir(knowledge_root, campaign_id)
    if not directory.is_dir():
        return []
    return [json.loads(p.read_text()) for p in sorted(directory.glob("[0-9][0-9][0-9][0-9].json"))]


def write_revision(
    knowledge_root: Path, derived: dict[str, Any], *, now: datetime | None = None
) -> tuple[Path | None, dict[str, Any]]:
    """Append `derived` as a new revision unless it equals the latest
    revision's knowledge. Returns (path or None if unchanged, revision)."""
    revisions = load_revisions(knowledge_root, derived["campaign_id"])
    latest = revisions[-1] if revisions else None
    if latest is not None and _knowledge_part(latest) == derived:
        return None, latest
    revision = {
        **derived,
        "revision": len(revisions) + 1,
        "previous_revision_hash": latest["content_hash"] if latest else None,
        "derived_at": (now or datetime.now(UTC)).isoformat(),
    }
    revision["content_hash"] = content_hash(revision)
    directory = ledger_dir(knowledge_root, derived["campaign_id"])
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{revision['revision']:04d}.json"
    if path.exists():
        raise FileExistsError(f"{path} already exists — ledger revisions are never overwritten")
    tmp = directory / f".{path.name}.tmp-{uuid4().hex[:8]}"
    tmp.write_text(json.dumps(revision, indent=2, sort_keys=True) + "\n")
    tmp.rename(path)
    return path, revision


# -- verification ----------------------------------------------------------------------------


def verify_revision(
    revision: dict[str, Any],
    campaign: Campaign,
    specs: dict[str, ExperimentSpec],
    records: list[dict[str, Any]],
    previous: dict[str, Any] | None,
    prospective_check: Callable[[dict[str, Any]], list[str]] | None = None,
) -> list[str]:
    """Every reason `revision` is not supported by its evidence (empty =
    verified). Statuses are re-derived from exactly the runs it cites, under
    the rules version the revision was written with. A cited prospective
    run must also pass `prospective_check` (its registration's exposure
    log, `app.research.prospective.verify_prospective_run`); without that
    check such a run is refused rather than trusted."""
    problems: list[str] = []
    if revision.get("ledger_schema_version") != LEDGER_SCHEMA_VERSION:
        return [f"unknown ledger_schema_version {revision.get('ledger_schema_version')!r}"]
    rules = revision.get("knowledge_rules_version")
    if rules not in SUPPORTED_RULES:
        return [f"unknown knowledge_rules_version {rules!r}"]
    if revision.get("content_hash") != content_hash(revision):
        problems.append("content_hash does not match the revision's content")
    expected_previous = previous["content_hash"] if previous else None
    if revision.get("previous_revision_hash") != expected_previous:
        problems.append("previous_revision_hash breaks the revision chain")
    if revision.get("campaign_id") != campaign.campaign_id:
        problems.append("campaign_id does not match the campaign")
    if revision.get("campaign_hash") != campaign_hash(campaign, specs):
        problems.append("campaign_hash does not match the campaign and its specs")
    if revision.get("scope") != "feature_level":
        problems.append("only feature_level knowledge is supported")

    registered = {h.id: h for h in campaign.hypotheses}
    entries = revision.get("hypotheses", [])
    if sorted(e.get("hypothesis_id") for e in entries) != sorted(registered):
        problems.append("hypotheses differ from the campaign's registered hypotheses")
        return problems

    by_run = {(r.get("experiment_id"), r.get("run_id")): r for r in records}
    for entry in entries:
        hid = entry["hypothesis_id"]
        hypothesis = registered[hid]
        if (entry.get("statement"), entry.get("prediction")) != (
            hypothesis.statement,
            hypothesis.prediction,
        ):
            problems.append(f"{hid}: statement or prediction differs from the registration")
        primary, own = _evidence_ids(campaign, hid)
        cited = []
        for reference in entry.get("evidence", []):
            key = (reference.get("experiment_id"), reference.get("run_id"))
            record = by_run.get(key)
            prospective = (
                rules >= 2
                and record is not None
                and record.get("kind") == "prospective_evaluation"
                and record.get("campaign_id") == campaign.campaign_id
                and record.get("hypothesis_id") == hid
            )
            if reference.get("experiment_id") not in {*primary, own} and not prospective:
                problems.append(f"{hid}: {key[0]} is not evidence registered for it")
                continue
            if record is None:
                problems.append(f"{hid}: run {key[0]}/{key[1]} does not exist")
                continue
            if prospective:
                if prospective_check is None:
                    problems.append(f"{hid}: prospective run {key[1]} cannot be checked here")
                else:
                    problems.extend(f"{hid}: {p}" for p in prospective_check(record))
            if record.get("status") != "completed":
                problems.append(f"{hid}: run {key[1]} did not complete")
                continue
            actual = _reference(record, reference.get("role", ""), rules)
            for field in (
                "segment",
                "verdict",
                "independent",
                "protocol",
                "spec_hash",
                "dataset_sha256",
                "evidence_class",
                "independence",
                "registration_id",
            ):
                if reference.get(field) != actual.get(field):
                    problems.append(f"{hid}: run {key[1]} {field} differs from its record")
            if record.get("kind") in (
                "incremental_comparison",
                "prospective_evaluation",
            ) and record.get("campaign_hash") != revision.get("campaign_hash"):
                problems.append(f"{hid}: run {key[1]} was recorded under another campaign hash")
            cited.append(record)
        rederived = derive_hypothesis(campaign, specs, hid, cited, rules)
        for field in (
            "knowledge_status",
            "validation_status",
            "prediction_sign",
            "direction",
            "experiment_id",
            "primary_evidence_ids",
            "evidence",
            "limitations",
        ):
            if entry.get(field) != rederived[field]:
                problems.append(f"{hid}: {field} is not what its cited evidence supports")
        problems.extend(repeat_conflicts(hid, rederived["evidence"]))
    return problems


def missing_evidence(
    revision: dict[str, Any], campaign: Campaign, records: list[dict[str, Any]]
) -> list[str]:
    """Completed runs bearing on a hypothesis that the revision doesn't cite
    — the ledger is behind the evidence (not wrong, but stale)."""
    stale = []
    for entry in revision.get("hypotheses", []):
        cited = {(e["experiment_id"], e["run_id"]) for e in entry.get("evidence", [])}
        rules = revision.get("knowledge_rules_version", KNOWLEDGE_RULES_VERSION)
        for e in collect_evidence(campaign, entry["hypothesis_id"], records, rules):
            if (e["experiment_id"], e["run_id"]) not in cited:
                stale.append(f"{entry['hypothesis_id']}: {e['experiment_id']}/{e['run_id']}")
    return stale
