"""OINK CORP prospective evaluation (protocol v1): freeze a campaign's
claims, reserve *future* UTC-day partitions for their validation and a
protected holdout, and evaluate them — once, with the existing comparison
protocol and Evaluator rules — only after the data exists.

ROLES.
- *Development*: everything outside a registration's partitions, including
  all data that existed when it was registered. Reusable; never
  independent confirmation.
- *Validation*: a registered partition evaluated against the frozen claims.
  Its first evaluation can be independent; later ones are repeats.
- *Protected holdout*: a later partition, evaluated only with an explicit
  `--protected-holdout` invocation, at most once, and only if nothing has
  ever been exposed over its period.

A partition is reserved (`app.research.reservations`) until its
evaluation completes: ordinary research commands cannot load any dataset
whose features or labels reach into it.

REGISTRATION. `<data_dir>/evaluations/<id>/registration/registration.json`
freezes:
- the campaign hash, each claim's comparison and candidate/reference spec
  hashes and hypothesis hashes, its prediction and label;
- the Evaluator and comparison protocol versions, plus a snapshot of
  Evaluator v1's minimum-evidence constants;
- the dataset and sample rules, the embargo and the partitions.

It is written once and identified by its hash. Any scientifically
meaningful change makes `verify_registration` fail, so the evaluation is
refused and a new registration (optionally `supersedes` the old one, for
lineage) is needed. Nothing is inherited from a predecessor.

PROSPECTIVE MEANS: every partition starts at a UTC midnight at least
`embargo` after `registered_at`; registration is refused if any existing
dataset already reaches into a partition; and evidence counts as
independent only if every shard evaluated was *generated* after the
registration (manifest `generated_at`). All of these are local-clock
facts: auditable, not externally witnessed. Committing the registration
to a remote Git repository is the only outside witness available.

TEMPORAL INTEGRITY. Partitions are whole UTC days, in order, non-
overlapping, separated by at least the embargo (the longest label
horizon) rounded up to whole days. Within a partition a row counts only
if `frame_time >= start` and `frame_time + label horizon <= end`, so no
label reaches past the partition.

EXPOSURE. Every attempt appends immutable events —
`authorized`, `started` (written *before* any label is read), then
`completed` or `failed`. A `started` without an outcome means exposure is
uncertain and is treated as exposed. Independence of a completed
evaluation (`independence`):
- `independent_prospective`: first exposure of this partition for this
  registration, no earlier exposure of an overlapping period for the same
  claim (hypothesis hash) or a registration in its lineage, every shard
  generated after registration;
- `repeat_exposure`: this registration's partition was exposed before;
- `claim_previously_exposed`: same claim or lineage, overlapping period;
- `undetermined`: provenance cannot establish it.

Overlap is judged on outcome windows (decision times plus label horizon),
never on dataset names or hashes.
"""

import json
import math
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

import pyarrow as pa
import pyarrow.compute as pc
from pydantic import Field, ValidationError

from app.research import evaluation
from app.research.campaign import Campaign, campaign_hash
from app.research.comparison import COMPARISON_VERSION, comparison_seed, evaluate_incremental
from app.research.continuity import SHARD_HORIZONS, shard_name
from app.research.dataset import code_fingerprint, get_git_commit
from app.research.evaluation import EVALUATOR_VERSION, SplitPlan, plan_split
from app.research.experiment import (
    DatasetRef,
    ExperimentSpec,
    _canonical_hash,
    _Frozen,
    hypothesis_hash,
    load_datasets,
    select,
    spec_hash,
)
from app.research.reservations import (
    Access,
    day_start,
    evaluations_root,
    outcome_window,
    read_events,
    read_registration_file,
    registration_dirs,
)
from app.research.runs import (
    RUNS_DIRNAME,
    check_experiment_identity,
    load_runs,
    new_run_id,
    write_run,
)

PROTOCOL_VERSION: Literal[1] = 1
EMBARGO = max(SHARD_HORIZONS.values())
#: A partition becomes eligible once every day has a shard, or this long
#: after it ends (label maturity plus the continuity worker's readiness
#: fallback); days still without a shard are then recorded as missing.
READY_AFTER = timedelta(days=2)
TIMESTAMP_TRUST = (
    "registered_at and event times come from the local clock; the files are append-only "
    "by convention and hash-identified, which makes changes detectable, not impossible. "
    "No external timestamping authority is involved."
)
_SLUG = r"^[a-z0-9][a-z0-9-]{0,79}$"
Role = Literal["validation", "holdout"]
Independence = Literal[
    "independent_prospective", "repeat_exposure", "claim_previously_exposed", "undetermined"
]


class ProspectiveError(ValueError):
    """A registration or evaluation the protocol does not allow."""


class Partition(_Frozen):
    role: Role
    start: date
    end: date  # exclusive


class FrozenClaim(_Frozen):
    hypothesis_id: str
    prediction: str
    comparison_id: str
    candidate_experiment_id: str
    reference_experiment_id: str
    candidate_spec_hash: str
    reference_spec_hash: str
    candidate_hypothesis_hash: str
    reference_hypothesis_hash: str
    label: str
    direction: Literal["long", "short"]


class EvaluationPlan(_Frozen):
    evaluator_version: int
    comparison_version: int
    minimum_evidence: dict[str, float]
    sample_interval_minutes: int
    embargo_minutes: int
    dataset_rule: str
    sample_rule: str


class RegistrationLike(Protocol):
    """What the generic parts of the protocol (partition rules, writing,
    exposure state) need from any registration kind — this one's campaign
    claims, or `app.research.truffle_evaluation`'s Truffle claims."""

    @property
    def registration_id(self) -> str: ...
    @property
    def registered_at(self) -> datetime: ...
    @property
    def supersedes(self) -> str | None: ...
    @property
    def partitions(self) -> list[Partition]: ...
    def model_dump(self, *, mode: str = ...) -> dict[str, Any]: ...


#: A registration file's `kind`; campaign-claim registrations predate the
#: field and have none (so their hashes are unchanged).
CAMPAIGN_KIND = None


class Registration(_Frozen):
    protocol_version: Literal[1]
    registration_id: str = Field(pattern=_SLUG)
    registered_at: datetime
    campaign_id: str
    campaign_hash: str
    supersedes: str | None = Field(default=None, pattern=_SLUG)
    claims: list[FrozenClaim] = Field(min_length=1, max_length=10)
    plan: EvaluationPlan
    partitions: list[Partition] = Field(min_length=1, max_length=2)
    timestamp_trust: str


def minimum_evidence() -> dict[str, float]:
    """Evaluator v1's evidence rules, frozen into every registration."""
    return {
        "MIN_UNIVERSE_PER_TIMESTAMP": evaluation.MIN_UNIVERSE_PER_TIMESTAMP,
        "MIN_TIMESTAMPS_PER_QUALIFYING_DAY": evaluation.MIN_TIMESTAMPS_PER_QUALIFYING_DAY,
        "MIN_DAYS_FOR_INFERENCE": evaluation.MIN_DAYS_FOR_INFERENCE,
        "MIN_SELECTED_TIMESTAMPS": evaluation.MIN_SELECTED_TIMESTAMPS,
        "MIN_DISTINCT_SELECTED_SYMBOLS": evaluation.MIN_DISTINCT_SELECTED_SYMBOLS,
        "MAX_TOP_SYMBOL_SHARE": evaluation.MAX_TOP_SYMBOL_SHARE,
        "MIN_CONSISTENT_DAY_FRACTION": evaluation.MIN_CONSISTENT_DAY_FRACTION,
        "BOOTSTRAP_RESAMPLES": evaluation.BOOTSTRAP_RESAMPLES,
        "CONFIDENCE_LEVEL": evaluation.CONFIDENCE_LEVEL,
    }


def registration_hash(registration: RegistrationLike) -> str:
    return _canonical_hash(registration.model_dump(mode="json"))


def _embargo_days() -> int:
    return math.ceil(EMBARGO / timedelta(days=1))


def earliest_start(registered_at: datetime) -> date:
    """The first UTC midnight at least `EMBARGO` after registration."""
    moment = registered_at + EMBARGO
    day = moment.date()
    return day if moment == day_start(day) else day + timedelta(days=1)


def partition_problems(registration: RegistrationLike) -> list[str]:
    problems = []
    roles = [p.role for p in registration.partitions]
    if roles not in (["validation"], ["validation", "holdout"]):
        problems.append("partitions must be one validation, optionally followed by one holdout")
    if registration.partitions[0].start < earliest_start(registration.registered_at):
        problems.append(
            f"the first partition must start on or after "
            f"{earliest_start(registration.registered_at)} "
            f"(registration time + {EMBARGO} embargo, at a UTC midnight): outcomes before that "
            "were not unseen at registration"
        )
    for p in registration.partitions:
        if p.end <= p.start:
            problems.append(f"{p.role}: end must be after start")
    for earlier, later in zip(registration.partitions, registration.partitions[1:], strict=False):
        if (later.start - earlier.end).days < _embargo_days():
            problems.append(
                f"{later.role} must start at least {_embargo_days()} day(s) after {earlier.role} "
                "ends (embargo: no label may span both)"
            )
    return problems


# -- building and registering ---------------------------------------------------------------


def build_registration(
    campaign: Campaign,
    specs: dict[str, ExperimentSpec],
    *,
    registration_id: str,
    hypothesis_ids: list[str],
    partitions: list[Partition],
    registered_at: datetime,
    supersedes: str | None = None,
) -> Registration:
    claims = []
    for hid in hypothesis_ids:
        hypothesis = next((h for h in campaign.hypotheses if h.id == hid), None)
        comparison = next((c for c in campaign.comparisons if c.hypothesis == hid), None)
        if hypothesis is None or comparison is None:
            raise ProspectiveError(f"{hid}: no such hypothesis with a registered comparison")
        candidate, reference = specs[comparison.candidate], specs[comparison.reference]
        claims.append(
            FrozenClaim(
                hypothesis_id=hid,
                prediction=hypothesis.prediction,
                comparison_id=comparison.comparison_id,
                candidate_experiment_id=candidate.experiment_id,
                reference_experiment_id=reference.experiment_id,
                candidate_spec_hash=spec_hash(candidate),
                reference_spec_hash=spec_hash(reference),
                candidate_hypothesis_hash=hypothesis_hash(candidate),
                reference_hypothesis_hash=hypothesis_hash(reference),
                label=candidate.label,
                direction=candidate.hypothesis.direction,
            )
        )
        if len(f"{registration_id}--{comparison.comparison_id}") > 80:
            raise ProspectiveError("registration_id too long for run ids; shorten it")
    return Registration(
        protocol_version=PROTOCOL_VERSION,
        registration_id=registration_id,
        registered_at=registered_at,
        campaign_id=campaign.campaign_id,
        campaign_hash=campaign_hash(campaign, specs),
        supersedes=supersedes,
        claims=claims,
        plan=EvaluationPlan(
            evaluator_version=EVALUATOR_VERSION,
            comparison_version=COMPARISON_VERSION,
            minimum_evidence=minimum_evidence(),
            sample_interval_minutes=15,
            embargo_minutes=int(EMBARGO.total_seconds() // 60),
            dataset_rule="the continuity worker's daily shards shard_s15m_YYYYMMDD of each "
            "partition day, as preserved; days without a shard are recorded as missing",
            sample_rule="a row counts only if frame_time >= partition start and "
            "frame_time + label horizon <= partition end",
        ),
        partitions=partitions,
        timestamp_trust=TIMESTAMP_TRUST,
    )


def _dataset_manifests(data_dir: Path) -> list[tuple[str, dict[str, Any]]]:
    found = []
    for path in sorted(data_dir.iterdir()) if data_dir.exists() else []:
        if path.is_dir() and not path.name.startswith(".") and (path / "manifest.json").exists():
            found.append((path.name, json.loads((path / "manifest.json").read_text())))
    return found


def register(data_dir: Path, registration: RegistrationLike) -> Path:
    """Write the registration once. Refuses invalid partitions, an existing
    id, an unknown predecessor, and partitions that existing data already
    reaches into (their outcomes would not be unseen)."""
    problems = partition_problems(registration)
    if problems:
        raise ProspectiveError("; ".join(problems))
    root = evaluations_root(data_dir)
    target = root / registration.registration_id
    if target.exists():
        raise ProspectiveError(f"registration {registration.registration_id} already exists")
    if registration.supersedes and not (root / registration.supersedes).is_dir():
        raise ProspectiveError(f"superseded registration {registration.supersedes} does not exist")
    for name, manifest in _dataset_manifests(data_dir):
        window = outcome_window(manifest)
        for p in registration.partitions:
            if window and window[0] < day_start(p.end) and day_start(p.start) <= window[1]:
                raise ProspectiveError(
                    f"dataset {name} already reaches into the {p.role} partition: "
                    "its outcomes are not unseen"
                )
    tmp = root / f".{registration.registration_id}.tmp-{uuid4().hex[:8]}"
    (tmp / "registration").mkdir(parents=True)
    stored = {
        "registration": registration.model_dump(mode="json"),
        "registration_hash": registration_hash(registration),
    }
    (tmp / "registration" / "registration.json").write_text(
        json.dumps(stored, indent=2, sort_keys=True) + "\n"
    )
    (tmp / "events").mkdir()
    tmp.rename(target)
    return target


@dataclass(frozen=True)
class Loaded:
    registration: Registration
    stored_hash: str
    events: list[dict[str, Any]]


def registration_kind(directory: Path) -> str | None:
    return read_registration_file(directory)["registration"].get("kind")


def load(data_dir: Path, registration_id: str) -> Loaded:
    directory = evaluations_root(data_dir) / registration_id
    if not directory.is_dir():
        raise ProspectiveError(f"no registration {registration_id}")
    stored = read_registration_file(directory)
    if stored["registration"].get("kind") is not CAMPAIGN_KIND:
        raise ProspectiveError(
            f"registration {registration_id} is a {stored['registration']['kind']} registration, "
            "not a campaign-claim registration"
        )
    try:
        registration = Registration.model_validate(stored["registration"])
    except ValidationError as exc:
        raise ProspectiveError(f"registration {registration_id} is invalid: {exc}") from exc
    return Loaded(registration, str(stored.get("registration_hash")), read_events(directory))


def load_all(data_dir: Path) -> list[Loaded]:
    """Every campaign-claim registration (other kinds have their own
    module; their reserved periods are still enforced by
    `app.research.reservations`, and their exposures by
    `other_kind_exposures`)."""
    return [
        load(data_dir, d.name)
        for d in registration_dirs(data_dir)
        if registration_kind(d) is CAMPAIGN_KIND
    ]


def other_kind_exposures(data_dir: Path) -> list[tuple[str, str, date, date]]:
    """(registration_id, role, start, end) of every exposed partition of a
    registration that is not a campaign-claim registration — so a holdout
    check here also sees periods exposed by other protocols."""
    found = []
    for directory in registration_dirs(data_dir):
        stored = read_registration_file(directory)["registration"]
        if stored.get("kind") is CAMPAIGN_KIND:
            continue
        events = read_events(directory)
        for p in stored.get("partitions", []):
            if exposed(events, p["role"]):
                found.append(
                    (
                        stored["registration_id"],
                        p["role"],
                        date.fromisoformat(p["start"]),
                        date.fromisoformat(p["end"]),
                    )
                )
    return found


def verify_registration(
    loaded: Loaded, campaign: Campaign, specs: dict[str, ExperimentSpec]
) -> list[str]:
    """Every reason the frozen registration no longer describes the same
    claims, data rules and evaluation (empty = verified)."""
    reg = loaded.registration
    problems = partition_problems(reg)
    if loaded.stored_hash != registration_hash(reg):
        problems.append("registration_hash does not match the registration: it was modified")
    if reg.campaign_id != campaign.campaign_id:
        problems.append("campaign_id differs")
    if reg.campaign_hash != campaign_hash(campaign, specs):
        problems.append("the campaign or its specs changed since registration")
    if (reg.plan.evaluator_version, reg.plan.comparison_version) != (
        EVALUATOR_VERSION,
        COMPARISON_VERSION,
    ):
        problems.append("the Evaluator or comparison protocol version changed")
    if reg.plan.minimum_evidence != minimum_evidence():
        problems.append("the Evaluator's minimum-evidence rules changed")
    for claim in reg.claims:
        comparison = next(
            (c for c in campaign.comparisons if c.comparison_id == claim.comparison_id), None
        )
        if comparison is None or (
            comparison.candidate,
            comparison.reference,
            comparison.hypothesis,
        ) != (
            claim.candidate_experiment_id,
            claim.reference_experiment_id,
            claim.hypothesis_id,
        ):
            problems.append(f"{claim.hypothesis_id}: its comparison changed")
            continue
        candidate, reference = specs[comparison.candidate], specs[comparison.reference]
        if (spec_hash(candidate), spec_hash(reference)) != (
            claim.candidate_spec_hash,
            claim.reference_spec_hash,
        ):
            problems.append(f"{claim.hypothesis_id}: an experiment specification changed")
    return problems


# -- exposure events and state ---------------------------------------------------------------


def append_event(data_dir: Path, registration_id: str, event: dict[str, Any]) -> dict[str, Any]:
    """Append an immutable event (its own directory, created exclusively,
    so two writers can never take the same sequence number)."""
    events_dir = evaluations_root(data_dir) / registration_id / "events"
    seq = len(read_events(evaluations_root(data_dir) / registration_id)) + 1
    record = {"seq": seq, "at": datetime.now(UTC).isoformat(), **event}
    tmp = events_dir / f".{seq:06d}.tmp-{uuid4().hex[:8]}"
    tmp.mkdir(parents=True)
    with (tmp / "event.json").open("w") as handle:
        handle.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    tmp.rename(events_dir / f"{seq:06d}")  # fails if that sequence number exists
    return record


def _partition(reg: RegistrationLike, role: str) -> Partition:
    partition = next((p for p in reg.partitions if p.role == role), None)
    if partition is None:
        raise ProspectiveError(f"registration {reg.registration_id} has no {role} partition")
    return partition


def partition_shards(
    data_dir: Path, partition: Partition
) -> list[tuple[date, dict[str, Any] | None]]:
    days = []
    day = partition.start
    while day < partition.end:
        path = data_dir / shard_name(day) / "manifest.json"
        days.append((day, json.loads(path.read_text()) if path.exists() else None))
        day += timedelta(days=1)
    return days


def attempts(events: list[dict[str, Any]], role: str) -> list[dict[str, Any]]:
    """Per attempt of `role`: its events and derived outcome."""
    by_attempt: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        if e.get("role") == role:
            by_attempt.setdefault(e["attempt_id"], []).append(e)
    summary = []
    for attempt_id, evs in by_attempt.items():
        types = [e["type"] for e in evs]
        if "completed" in types:
            outcome = "completed"
        elif "failed" in types:
            outcome = "failed" if "started" in types else "failed_before_exposure"
        elif "started" in types:
            outcome = "exposure_uncertain"
        else:
            outcome = "authorized_only"
        summary.append({"attempt_id": attempt_id, "outcome": outcome, "events": evs})
    return summary


def exposed(events: list[dict[str, Any]], role: str) -> bool:
    return any(
        a["outcome"] not in ("authorized_only", "failed_before_exposure")
        for a in attempts(events, role)
    )


def partition_state(loaded: Loaded, role: str, data_dir: Path, now: datetime) -> dict[str, Any]:
    return registration_partition_state(loaded.registration, loaded.events, role, data_dir, now)


def registration_partition_state(
    registration: RegistrationLike,
    events: list[dict[str, Any]],
    role: str,
    data_dir: Path,
    now: datetime,
) -> dict[str, Any]:
    """`partition_state` for any registration kind."""
    partition = _partition(registration, role)
    shards = partition_shards(data_dir, partition)
    present = [d for d, m in shards if m is not None]
    # Eligible once the partition has ended and every day has a shard, or
    # READY_AFTER later regardless (missing days are then recorded).
    complete = len(present) == len(shards)
    eligible_from = day_start(partition.end) + (timedelta(0) if complete else READY_AFTER)
    history = attempts(events, role)
    if history:
        state = history[-1]["outcome"]
    elif now >= eligible_from:
        state = "eligible"
    else:
        state = "awaiting_data"
    return {
        "role": role,
        "start": partition.start.isoformat(),
        "end": partition.end.isoformat(),
        "state": state,
        "eligible_from": eligible_from.isoformat(),
        "days": len(shards),
        "days_with_shard": len(present),
        "exposed": exposed(events, role),
        "attempts": len(history),
    }


# -- independence --------------------------------------------------------------------------


def _lineage(all_loaded: list[Loaded], registration_id: str) -> set[str]:
    parent = {x.registration.registration_id: x.registration.supersedes for x in all_loaded}
    related = {registration_id}
    changed = True
    while changed:
        changed = False
        for child, sup in parent.items():
            if sup in related and child not in related:
                related.add(child)
                changed = True
            if child in related and sup and sup not in related:
                related.add(sup)
                changed = True
    return related


def independence(
    all_loaded: list[Loaded],
    loaded: Loaded,
    role: str,
    shard_manifests: list[dict[str, Any]],
    before_seq: int,
) -> tuple[Independence, dict[str, Any]]:
    """How independent an evaluation of `role` is, from everything recorded
    before event `before_seq` of this registration and all other
    registrations' exposures. Overlap is judged on periods, not names."""
    reg = loaded.registration
    partition = _partition(reg, role)
    start, end = day_start(partition.start), day_start(partition.end)
    own_prior = any(
        a["outcome"] not in ("authorized_only", "failed_before_exposure")
        and min(e["seq"] for e in a["events"]) < before_seq
        for a in attempts(loaded.events, role)
    )
    claims = {c.candidate_hypothesis_hash for c in reg.claims}
    lineage = _lineage(all_loaded, reg.registration_id)
    related: list[str] = []
    other: list[str] = []
    for other_loaded in all_loaded:
        other_reg = other_loaded.registration
        if other_reg.registration_id == reg.registration_id:
            continue
        for p in other_reg.partitions:
            if not (day_start(p.start) < end and start < day_start(p.end)):
                continue
            if not exposed(other_loaded.events, p.role):
                continue
            same_claim = claims & {c.candidate_hypothesis_hash for c in other_reg.claims}
            target = related if same_claim or other_reg.registration_id in lineage else other
            target.append(f"{other_reg.registration_id}/{p.role}")
    late = [
        m.get("shard_day")
        for m in shard_manifests
        if not m.get("generated_at")
        or datetime.fromisoformat(m["generated_at"]) <= reg.registered_at
    ]
    details = {
        "prior_exposure_same_registration": own_prior,
        "overlapping_exposures_same_claim_or_lineage": related,
        "overlapping_exposures_other_claims": other,
        "shards_not_generated_after_registration": late,
    }
    if own_prior:
        return "repeat_exposure", details
    if related:
        return "claim_previously_exposed", details
    if late or not shard_manifests:
        return "undetermined", details
    return "independent_prospective", details


# -- evaluation ----------------------------------------------------------------------------


def _filter_partition(table: pa.Table, partition: Partition, horizon_minutes: int) -> pa.Table:
    start = day_start(partition.start)
    last = day_start(partition.end) - timedelta(minutes=horizon_minutes)
    times = table.column("frame_time")
    keep = pc.and_(
        pc.greater_equal(times, pa.scalar(start, type=times.type)),
        pc.less_equal(times, pa.scalar(last, type=times.type)),
    )
    return table.filter(keep)


def evaluate(
    data_dir: Path,
    campaign: Campaign,
    specs: dict[str, ExperimentSpec],
    registration_id: str,
    role: Role,
    *,
    now: datetime | None = None,
    protected_holdout: bool = False,
    acknowledge_prior_exposure: bool = False,
) -> dict[str, Any]:
    """Run one eligible evaluation of a verified registration. Records
    `authorized`, then `started` before any outcome label is read, then
    `completed` (with run references) or `failed`. Returns the completed
    event."""
    now = now or datetime.now(UTC)
    all_loaded = load_all(data_dir)
    loaded = next(
        (x for x in all_loaded if x.registration.registration_id == registration_id), None
    )
    if loaded is None:
        raise ProspectiveError(f"no registration {registration_id}")
    problems = verify_registration(loaded, campaign, specs)
    if problems:
        raise ProspectiveError("registration does not verify: " + "; ".join(problems))
    reg = loaded.registration
    partition = _partition(reg, role)
    if role == "holdout" and not protected_holdout:
        raise ProspectiveError("the protected holdout needs an explicit --protected-holdout")
    state = partition_state(loaded, role, data_dir, now)
    if state["state"] == "awaiting_data":
        raise ProspectiveError(f"{role} is not eligible until {state['eligible_from']}")
    if role == "holdout":
        if exposed(loaded.events, "holdout"):
            raise ProspectiveError("the protected holdout was already exposed; it cannot be re-run")
        for other in all_loaded:
            for p in other.registration.partitions:
                if (
                    other is not loaded
                    and day_start(p.start) < day_start(partition.end)
                    and day_start(partition.start) < day_start(p.end)
                    and exposed(other.events, p.role)
                ):
                    raise ProspectiveError(
                        "the holdout period was already exposed by "
                        f"{other.registration.registration_id}"
                    )
        for other_id, other_role, start, end in other_kind_exposures(data_dir):
            if start < partition.end and partition.start < end:
                raise ProspectiveError(
                    f"the holdout period was already exposed by {other_id}/{other_role}"
                )
    elif exposed(loaded.events, role) and not acknowledge_prior_exposure:
        raise ProspectiveError(
            f"{role} was already exposed for this registration; re-running it yields "
            "non-independent evidence (pass --acknowledge-prior-exposure)"
        )

    shards = [(d, m) for d, m in partition_shards(data_dir, partition) if m is not None]
    if not shards:
        raise ProspectiveError(f"no shard exists for any day of the {role} partition")
    attempt_id = uuid4().hex[:12]
    datasets = [DatasetRef(name=shard_name(d), data_sha256=m["data_sha256"]) for d, m in shards]
    authorized = append_event(
        data_dir,
        registration_id,
        {
            "type": "authorized",
            "attempt_id": attempt_id,
            "role": role,
            "partition": {"start": partition.start.isoformat(), "end": partition.end.isoformat()},
            "datasets": [d.model_dump() for d in datasets],
            "missing_days": [
                d.isoformat() for d, m in partition_shards(data_dir, partition) if m is None
            ],
        },
    )
    kind, details = independence(
        all_loaded, loaded, role, [m for _, m in shards], authorized["seq"]
    )
    append_event(
        data_dir,
        registration_id,
        {
            "type": "started",
            "attempt_id": attempt_id,
            "role": role,
            "note": "outcome labels may be read from here on",
        },
    )
    access = Access(registration_id, role, attempt_id)
    runs: list[str] = []
    window: list[str | None] = [None, None]
    try:
        r_hash = registration_hash(reg)
        for claim in reg.claims:
            candidate = specs[claim.candidate_experiment_id].model_copy(
                update={"datasets": datasets}
            )
            reference = specs[claim.reference_experiment_id].model_copy(
                update={"datasets": datasets}
            )
            loaded_data = load_datasets(candidate, data_dir, access=access)
            table = _filter_partition(loaded_data.table, partition, loaded_data.horizon_minutes)
            cand_obs, ref_obs = select(candidate, table), select(reference, table)
            base = plan_split(cand_obs.frame_time)
            split = SplitPlan(
                base.qualifying_days, base.excluded_days, [], base.qualifying_days, None
            )
            seed = comparison_seed(
                claim.candidate_spec_hash, claim.reference_spec_hash, "validation"
            )
            report = evaluate_incremental(
                cand_obs,
                ref_obs,
                segment="validation",
                split=split,
                horizon_minutes=loaded_data.horizon_minutes,
                seed=seed,
            )
            times = table.column("frame_time").to_pylist()
            if times:
                window = [
                    min(times).isoformat(),
                    (max(times) + timedelta(minutes=loaded_data.horizon_minutes)).isoformat(),
                ]
            experiment_id = f"{registration_id}--{claim.comparison_id}"
            definition = {"registration_hash": r_hash, "role": role, "claim": claim.model_dump()}
            runs_root = data_dir / RUNS_DIRNAME
            check_experiment_identity(
                load_runs(runs_root), experiment_id, _canonical_hash(definition)
            )
            executed_at = datetime.now(UTC)
            record = {
                "run_id": new_run_id(executed_at),
                "executed_at": executed_at.isoformat(),
                "kind": "prospective_evaluation",
                "experiment_id": experiment_id,
                "spec_hash": _canonical_hash(definition),
                "hypothesis_hash": claim.candidate_hypothesis_hash,
                "campaign_id": reg.campaign_id,
                "campaign_hash": reg.campaign_hash,
                "hypothesis_id": claim.hypothesis_id,
                "comparison_id": claim.comparison_id,
                "registration_id": registration_id,
                "registration_hash": r_hash,
                "role": role,
                "segment": role,
                "partition": {
                    "start": partition.start.isoformat(),
                    "end": partition.end.isoformat(),
                },
                "attempt_id": attempt_id,
                "independence": kind,
                "independence_details": details,
                "candidate": {
                    "experiment_id": claim.candidate_experiment_id,
                    "spec_hash": claim.candidate_spec_hash,
                    "hypothesis_hash": claim.candidate_hypothesis_hash,
                },
                "reference": {
                    "experiment_id": claim.reference_experiment_id,
                    "spec_hash": claim.reference_spec_hash,
                    "hypothesis_hash": claim.reference_hypothesis_hash,
                },
                "label": claim.label,
                "evaluator_version": EVALUATOR_VERSION,
                "comparison_version": COMPARISON_VERSION,
                "protocol_version": PROTOCOL_VERSION,
                "code_fingerprint": code_fingerprint(),
                "git_commit": get_git_commit(),
                "seed": seed,
                "datasets": [
                    {
                        "name": d.name,
                        "data_sha256": d.data_sha256,
                        "generated_at": m.get("generated_at"),
                    }
                    for d, (_, m) in zip(datasets, shards, strict=True)
                ],
                "evaluated_window": window,
                "report": report,
                "status": "completed",
                "error": None,
            }
            write_run(runs_root, record, definition)
            runs.append(f"{experiment_id}/{record['run_id']}")
    except Exception as exc:
        append_event(
            data_dir,
            registration_id,
            {
                "type": "failed",
                "attempt_id": attempt_id,
                "role": role,
                "error": f"{type(exc).__name__}: {exc}"[-2000:],
                "exposure": "possible: labels may have been read before the failure",
                "runs_written": runs,
            },
        )
        raise
    return append_event(
        data_dir,
        registration_id,
        {
            "type": "completed",
            "attempt_id": attempt_id,
            "role": role,
            "independence": kind,
            "independence_details": details,
            "runs": runs,
            "outcome_window": window,
        },
    )


def verify_prospective_run(data_dir: Path, record: dict[str, Any]) -> list[str]:
    """Is a prospective run backed by its registration's exposure log?
    (Used by the knowledge ledger before counting it.)"""
    try:
        loaded = load(data_dir, str(record.get("registration_id")))
    except (ProspectiveError, ValueError) as exc:
        return [f"{record.get('run_id')}: {exc}"]
    problems = []
    if (
        loaded.stored_hash != registration_hash(loaded.registration)
        or record.get("registration_hash") != loaded.stored_hash
    ):
        problems.append(f"{record.get('run_id')}: registration hash mismatch")
    completed = [
        e
        for e in loaded.events
        if e.get("type") == "completed" and e.get("attempt_id") == record.get("attempt_id")
    ]
    if not completed:
        problems.append(f"{record.get('run_id')}: no completed exposure event for its attempt")
    else:
        event = completed[0]
        if f"{record['experiment_id']}/{record['run_id']}" not in event.get("runs", []):
            problems.append(f"{record.get('run_id')}: not listed by its completed event")
        if event.get("independence") != record.get("independence"):
            problems.append(f"{record.get('run_id')}: independence differs from the exposure log")
    return problems


def registration_summary(loaded: Loaded, data_dir: Path, now: datetime) -> dict[str, Any]:
    return {
        "registration_id": loaded.registration.registration_id,
        "registration_hash": loaded.stored_hash,
        "registered_at": loaded.registration.registered_at.isoformat(),
        "hypotheses": [c.hypothesis_id for c in loaded.registration.claims],
        "partitions": [
            partition_state(loaded, p.role, data_dir, now) for p in loaded.registration.partitions
        ],
    }


__all__ = [
    "Partition",
    "ProspectiveError",
    "Registration",
    "build_registration",
    "evaluate",
    "independence",
    "load",
    "load_all",
    "partition_state",
    "register",
    "verify_prospective_run",
    "verify_registration",
]
