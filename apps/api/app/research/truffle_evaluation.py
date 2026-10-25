"""OINK CORP prospective Truffle evaluation (Truffle protocol v1).

Question: do coins qualified as experimental Truffles show a measurable
difference in subsequent market-relative behaviour from a predeclared
reference population of the same timestamps? Observational, not a trading
simulation: no entry price, fees, sizing or execution is assumed.

It is a second registration *kind* in the same protocol infrastructure as
`app.research.prospective` (campaign claims): the same
`<data_dir>/evaluations/<id>/` layout, written once by
`prospective.register` (partition rules, embargo, refusal when data already
reaches into a partition), the same append-only exposure events
(`authorized` -> `started` before any label is read -> `completed` /
`failed`), the same reservations (`app.research.reservations` refuses any
other loader), and the same Evaluator v1 statistics. What differs is the
claim: instead of an experiment spec it freezes a ranking candidate and a
qualification spec by hash, and selection is computed exactly as live
Sniffer computes it (`app.ranking.qualification` over the candidate's
eligible universe), so the evaluated Truffles are the live ones.

A registration binds exact hashes. If a ranking candidate, a qualification
spec, the qualification semantics, Evaluator v1's rules or the temporal
contract change, `verify_truffle_registration` fails and the evaluation is
refused: a changed specification never inherits a registration and needs
a new one (and a new spec id).

See docs/TRUFFLE_EVALUATION.md for the protocol in prose.
"""

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import pyarrow as pa
import pyarrow.compute as pc
from pydantic import Field, ValidationError

from app.domain.temporal import TEMPORAL_CONTRACT_VERSION
from app.ranking import qualification as qual
from app.ranking.qualification import (
    QUALIFICATION_EVALUATOR_VERSION,
    QualificationSpec,
    load_qualification,
    qualification_hash,
    qualified,
)
from app.ranking.scoring import (
    MIN_CROSS_SECTION,
    RankingCandidate,
    candidate_hash,
    eligible_indices,
    load_candidate_file,
)
from app.research import evaluation
from app.research.continuity import shard_name
from app.research.dataset import code_fingerprint, get_git_commit
from app.research.evaluation import CONFIDENCE_LEVEL, EVALUATOR_VERSION, plan_split
from app.research.experiment import DatasetRef, Observations, _canonical_hash, _Frozen, load_columns
from app.research.prospective import (
    EMBARGO,
    TIMESTAMP_TRUST,
    Partition,
    ProspectiveError,
    append_event,
    attempts,
    exposed,
    minimum_evidence,
    partition_problems,
    partition_shards,
    register,
    registration_hash,
    registration_partition_state,
)
from app.research.reservations import (
    Access,
    day_start,
    evaluations_root,
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

TRUFFLE_KIND: Literal["truffle_qualification"] = "truffle_qualification"
TRUFFLE_PROTOCOL_VERSION: Literal[1] = 1
PRIMARY_LABEL = "forward_relative_return_1h"
SECONDARY_LABELS = ("forward_relative_return_4h",)
#: The primary family: one claim per direction.
FAMILY_SIZE = 2
#: Bonferroni over the family: each claim's interval at 1 - (1 - 0.90) / 2.
ADJUSTED_CONFIDENCE = 1 - (1 - CONFIDENCE_LEVEL) / FAMILY_SIZE
_SLUG = r"^[a-z0-9][a-z0-9-]{0,79}$"
Role = Literal["validation", "holdout"]
Decision = Literal[
    "insufficient_evidence",
    "consistent_with_improvement",
    "inconsistent_with_improvement",
    "inconclusive",
]

QUESTION = (
    "Do coins qualified as experimental Truffles by the live qualification specs show a "
    "different subsequent market-relative return, in their direction, than the eligible "
    "universe of the same timestamps?"
)
LIMITATIONS = (
    "Observational: forward returns start at the close of the frame's 5m candle (time T), "
    "while live Truffles are published after the decision time (T+60s) plus analysis "
    "(about T+110s). The result is not an achievable entry return.",
    "No trading costs, slippage, funding payments, spread, capacity or execution are "
    "modeled; a difference in relative returns is not profitability.",
    "The live frame gates are not reproduced: freshness is a wall-clock notion of the live "
    "page, and frame completeness is not recorded in the research shards, so every sampled "
    "frame is evaluated with the instrument conditions alone.",
    "Frames are sampled every 15 minutes (the continuity shards); Truffles of other frames "
    "are not evaluated.",
    "The reference population (eligible universe) includes the Truffles themselves; it is "
    "not an independent control group. The non-Truffle comparison is secondary.",
    "Forward relative returns are relative to the cross-sectional median of the frame; "
    "Truffles missing a forward outcome (e.g. delisting) are excluded after selection — a "
    "survivorship risk that is counted, not corrected.",
    "The ranking models' reversal thesis is unvalidated; exploratory development results "
    "(2026-10-12) pointed to continuation. Neither is assumed here.",
    "One validation period of about a month covers few market regimes.",
)


class ProtocolError(ProspectiveError):
    """A Truffle registration or evaluation the protocol does not allow."""


class TruffleClaim(_Frozen):
    hypothesis_id: str = Field(pattern=r"^T-(LONG|SHORT)$")
    direction: Literal["long", "short"]
    #: The registered sign: Truffles are predicted to do better in their
    #: direction. Structured, never parsed from the statement.
    prediction_direction: Literal["positive"]
    statement: str
    #: Directional utility: +label for Long, -label for Short.
    utility_sign: Literal[1, -1]
    ranking_candidate: str = Field(pattern=_SLUG)
    ranking_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    qualification_id: str = Field(pattern=_SLUG)
    qualification_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    primary_label: str
    secondary_labels: list[str]


class TrufflePlan(_Frozen):
    evaluator_version: int
    minimum_evidence: dict[str, float]
    qualification_evaluator_version: int
    ranking_model_family: str
    min_cross_section: int
    temporal_contract_version: int
    sample_interval_minutes: int
    embargo_minutes: int
    dataset_rule: str
    sample_rule: str
    universe_rule: str
    selection_rule: str
    unit_of_observation: str
    primary_reference: str
    secondary_analyses: list[str]
    statistical_method: str
    multiplicity: dict[str, Any]
    decision_rules: dict[str, str]
    missing_data: str
    zero_truffle_frames: str
    overlapping_horizons: str
    variable_truffle_counts: str
    exploratory_policy: str


class TruffleRegistration(_Frozen):
    kind: Literal["truffle_qualification"]
    protocol_version: Literal[1]
    registration_id: str = Field(pattern=_SLUG)
    registered_at: datetime
    supersedes: str | None = Field(default=None, pattern=_SLUG)
    question: str
    claims: list[TruffleClaim] = Field(min_length=1, max_length=2)
    plan: TrufflePlan
    partitions: list[Partition] = Field(min_length=1, max_length=2)
    limitations: list[str]
    timestamp_trust: str


@dataclass(frozen=True)
class LoadedTruffle:
    registration: TruffleRegistration
    stored_hash: str
    events: list[dict[str, Any]]


# -- building and registering -----------------------------------------------------------------


def _load_specs(
    candidates_dir: Path, qualifications_dir: Path, candidate_id: str, qualification_id: str
) -> tuple[RankingCandidate, QualificationSpec]:
    try:
        candidate = load_candidate_file(candidates_dir / f"{candidate_id}.json")
    except (OSError, ValidationError) as exc:
        raise ProtocolError(f"ranking candidate {candidate_id}: {exc}") from exc
    if candidate.candidate_id != candidate_id:
        raise ProtocolError(f"{candidate_id}.json declares {candidate.candidate_id!r}")
    try:
        spec = load_qualification(qualifications_dir, qualification_id)
    except ValueError as exc:
        raise ProtocolError(str(exc)) from exc
    if (spec.ranking_candidate, spec.direction) != (candidate_id, candidate.direction):
        raise ProtocolError(
            f"{qualification_id} applies to {spec.ranking_candidate} ({spec.direction}), "
            f"not {candidate_id} ({candidate.direction})"
        )
    return candidate, spec


def _plan() -> TrufflePlan:
    return TrufflePlan(
        evaluator_version=EVALUATOR_VERSION,
        minimum_evidence=minimum_evidence(),
        qualification_evaluator_version=QUALIFICATION_EVALUATOR_VERSION,
        ranking_model_family="linear_weighted_v1",
        min_cross_section=MIN_CROSS_SECTION,
        temporal_contract_version=TEMPORAL_CONTRACT_VERSION,
        sample_interval_minutes=15,
        embargo_minutes=int(EMBARGO.total_seconds() // 60),
        dataset_rule="the continuity worker's daily shards shard_s15m_YYYYMMDD of each partition "
        "day, as preserved; a day without a shard, or whose shard was not produced under the "
        "frozen temporal contract version, is recorded as missing",
        sample_rule="a row counts only if frame_time >= partition start and frame_time + label "
        "horizon <= partition end",
        universe_rule="per sampled frame: its persisted members (the shard rows) whose every "
        "ranking-candidate feature is finite — the live ranking's eligible universe; a frame "
        f"with fewer than {MIN_CROSS_SECTION} such coins is not evaluated",
        selection_rule="a coin is a Truffle at a frame if it passes every instrument condition "
        "of the frozen qualification spec, evaluated by app.ranking.qualification (qualification "
        f"evaluator v{QUALIFICATION_EVALUATOR_VERSION}) over that universe — the live rule",
        unit_of_observation="the timestamp: per sampled frame, the mean directional utility of "
        "its Truffles minus that of its eligible universe; coins are never independent "
        "observations",
        primary_reference="the same-timestamp eligible universe (which includes the Truffles), "
        f"directional utility of {PRIMARY_LABEL}",
        secondary_analyses=[
            "the same comparison at forward_relative_return_4h",
            f"Truffles versus the same-timestamp eligible non-Truffles, {PRIMARY_LABEL}",
            "qualification frequency and coverage (descriptive)",
        ],
        statistical_method="Evaluator v1 (app.research.evaluation.evaluate, validation segment, "
        "every qualifying day of the partition): timestamp-weighted mean spread with a "
        f"{CONFIDENCE_LEVEL:.0%} day-block bootstrap interval "
        f"({evaluation.BOOTSTRAP_RESAMPLES} resamples, deterministic seed from the registration "
        "hash and claim), minimum-evidence rules frozen above; plus the same day-block interval "
        f"at {ADJUSTED_CONFIDENCE:.0%} for the multiplicity adjustment",
        multiplicity={
            "primary_family": ["T-LONG", "T-SHORT"],
            "family_size": FAMILY_SIZE,
            "method": "Bonferroni",
            "evaluator_confidence": CONFIDENCE_LEVEL,
            "adjusted_confidence": ADJUSTED_CONFIDENCE,
            "rule": "a primary claim counts as consistent (or inconsistent) with improvement only "
            "if Evaluator v1's verdict is directional and the adjusted interval also excludes "
            "zero; Long and Short are separate claims, never pooled into one 'Truffles work' "
            "claim",
            "secondary": "secondary horizons, the non-Truffle comparison and coverage are "
            "reported without verdicts and never change a decision",
        },
        decision_rules={
            "insufficient_evidence": "Evaluator v1 reports insufficient_data (fewer than its "
            "minimum days, timestamps with Truffles, or distinct Truffle coins)",
            "consistent_with_improvement": "Evaluator v1 positive_evidence and the adjusted "
            "interval's lower bound > 0",
            "inconsistent_with_improvement": "Evaluator v1 negative_evidence and the adjusted "
            "interval's upper bound < 0 (Truffles did worse in their direction)",
            "inconclusive": "anything else, including a directional Evaluator verdict that does "
            "not survive the adjustment",
        },
        missing_data="a Truffle without the label is excluded after selection and counted "
        "(selected_missing_outcome); the universe mean uses coins with the label; days without "
        "a shard are listed as missing, never filled",
        zero_truffle_frames="valid; counted in coverage (timestamps_without_selection), "
        "contribute no spread to the primary estimate, and no qualified-group return is "
        "fabricated for them",
        overlapping_horizons="15-minute sampling with a 1h (4h) horizon overlaps within a day; "
        "whole days are resampled together and effective_independent_windows is reported",
        variable_truffle_counts="each timestamp weighs equally whatever its Truffle count (the "
        "mean within the timestamp), so busy frames do not dominate",
        exploratory_policy="anything not declared here is exploratory, labelled so, and never "
        "changes a decision",
    )


def build_truffle_registration(
    candidates_dir: Path,
    qualifications_dir: Path,
    *,
    registration_id: str,
    partitions: list[Partition],
    registered_at: datetime,
    pairs: Sequence[tuple[str, str]],
    supersedes: str | None = None,
) -> TruffleRegistration:
    """Freeze one claim per (ranking candidate, qualification spec) pair."""
    claims = []
    for candidate_id, qualification_id in pairs:
        candidate, spec = _load_specs(
            candidates_dir, qualifications_dir, candidate_id, qualification_id
        )
        long = candidate.direction == "long"
        claims.append(
            TruffleClaim(
                hypothesis_id="T-LONG" if long else "T-SHORT",
                direction=candidate.direction,
                prediction_direction="positive",
                statement=(
                    f"{'Long' if long else 'Short'} Truffles ({qualification_id}) have a higher "
                    f"same-timestamp mean of {'+' if long else '-'}{PRIMARY_LABEL} than the "
                    "eligible universe"
                ),
                utility_sign=1 if long else -1,
                ranking_candidate=candidate_id,
                ranking_spec_hash=candidate_hash(candidate),
                qualification_id=qualification_id,
                qualification_spec_hash=qualification_hash(spec),
                primary_label=PRIMARY_LABEL,
                secondary_labels=list(SECONDARY_LABELS),
            )
        )
    if len({c.direction for c in claims}) != len(claims):
        raise ProtocolError("one claim per direction")
    if len(f"{registration_id}--t-short") > 80:
        raise ProtocolError("registration_id too long for run ids; shorten it")
    return TruffleRegistration(
        kind=TRUFFLE_KIND,
        protocol_version=TRUFFLE_PROTOCOL_VERSION,
        registration_id=registration_id,
        registered_at=registered_at,
        supersedes=supersedes,
        question=QUESTION,
        claims=claims,
        plan=_plan(),
        partitions=partitions,
        limitations=list(LIMITATIONS),
        timestamp_trust=TIMESTAMP_TRUST,
    )


def reservation_conflicts(
    data_dir: Path, partitions: Sequence[Partition], *, registration_id: str | None = None
) -> list[str]:
    """Every existing registration partition (any kind) that `partitions`
    overlap or come within the embargo of. Access grants are per
    registration, so an overlapping window could only be evaluated by
    reading another study's reserved days — which this protocol never
    does."""
    gap = timedelta(days=math.ceil(EMBARGO / timedelta(days=1)))
    conflicts = []
    for directory in registration_dirs(data_dir):
        stored = read_registration_file(directory)["registration"]
        if stored.get("registration_id") == registration_id:
            continue  # the writer reports an existing id itself
        for other in stored.get("partitions", []):
            start, end = date.fromisoformat(other["start"]), date.fromisoformat(other["end"])
            for p in partitions:
                if p.start < end + gap and start < p.end + gap:
                    conflicts.append(
                        f"{p.role} {p.start}..{p.end} is within {gap.days} day(s) of "
                        f"{stored['registration_id']}/{other['role']} {start}..{end}"
                    )
    return conflicts


def register_truffles(data_dir: Path, registration: TruffleRegistration) -> Path:
    """Write once, through the shared protocol writer (partition, embargo
    and unseen-data checks included), after refusing any window that
    touches another registration's reserved periods."""
    conflicts = reservation_conflicts(
        data_dir, registration.partitions, registration_id=registration.registration_id
    )
    if conflicts:
        raise ProtocolError("; ".join(conflicts))
    return register(data_dir, registration)


def load_truffle(data_dir: Path, registration_id: str) -> LoadedTruffle:
    directory = evaluations_root(data_dir) / registration_id
    if not directory.is_dir():
        raise ProtocolError(f"no registration {registration_id}")
    stored = read_registration_file(directory)
    if stored["registration"].get("kind") != TRUFFLE_KIND:
        raise ProtocolError(f"registration {registration_id} is not a Truffle registration")
    try:
        registration = TruffleRegistration.model_validate(stored["registration"])
    except ValidationError as exc:
        raise ProtocolError(f"registration {registration_id} is invalid: {exc}") from exc
    return LoadedTruffle(registration, str(stored.get("registration_hash")), read_events(directory))


def load_all_truffles(data_dir: Path) -> list[LoadedTruffle]:
    return [
        load_truffle(data_dir, d.name)
        for d in registration_dirs(data_dir)
        if read_registration_file(d)["registration"].get("kind") == TRUFFLE_KIND
    ]


def verify_truffle_registration(
    loaded: LoadedTruffle, candidates_dir: Path, qualifications_dir: Path
) -> list[str]:
    """Every reason the registration no longer describes the same claims
    and evaluation (empty = verified)."""
    reg = loaded.registration
    problems = partition_problems(reg)
    if loaded.stored_hash != registration_hash(reg):
        problems.append("registration_hash does not match the registration: it was modified")
    expected = _plan()
    for field in (
        "evaluator_version",
        "minimum_evidence",
        "qualification_evaluator_version",
        "temporal_contract_version",
        "min_cross_section",
    ):
        if getattr(reg.plan, field) != getattr(expected, field):
            problems.append(f"{field} changed since registration")
    for claim in reg.claims:
        try:
            candidate, spec = _load_specs(
                candidates_dir, qualifications_dir, claim.ranking_candidate, claim.qualification_id
            )
        except ProtocolError as exc:
            problems.append(f"{claim.hypothesis_id}: {exc}")
            continue
        if candidate_hash(candidate) != claim.ranking_spec_hash:
            problems.append(
                f"{claim.hypothesis_id}: ranking candidate {claim.ranking_candidate} changed — a "
                "changed model does not inherit this registration"
            )
        if qualification_hash(spec) != claim.qualification_spec_hash:
            problems.append(
                f"{claim.hypothesis_id}: qualification spec {claim.qualification_id} changed — a "
                "changed policy does not inherit this registration"
            )
    return problems


# -- lifecycle ------------------------------------------------------------------------------


def truffle_status(loaded: LoadedTruffle, data_dir: Path, now: datetime) -> dict[str, Any]:
    """Registered -> awaiting_data -> eligible -> evaluated (with each
    claim's decision, e.g. insufficient_evidence), from the exposure log and
    run records only."""
    runs = [
        r
        for r in load_runs(data_dir / RUNS_DIRNAME).records
        if r.get("kind") == "prospective_truffle_evaluation"
        and r.get("registration_id") == loaded.registration.registration_id
    ]
    partitions = []
    for p in loaded.registration.partitions:
        state = registration_partition_state(
            loaded.registration, loaded.events, p.role, data_dir, now
        )
        decisions = {
            r["hypothesis_id"]: r["decision"]
            for r in runs
            if r.get("role") == p.role and r.get("status") == "completed"
        }
        partitions.append(
            {**state, "lifecycle": _lifecycle(state["state"]), "decisions": decisions}
        )
    return {
        "registration_id": loaded.registration.registration_id,
        "registration_hash": loaded.stored_hash,
        "kind": TRUFFLE_KIND,
        "registered_at": loaded.registration.registered_at.isoformat(),
        "claims": [c.model_dump() for c in loaded.registration.claims],
        "partitions": partitions,
    }


def _lifecycle(state: str) -> str:
    return {
        "awaiting_data": "registered_awaiting_data",
        "eligible": "eligible_for_evaluation",
        "completed": "evaluated",
    }.get(state, state)


# -- selection --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Selection:
    observations: Observations
    timestamps: int
    timestamps_universe_too_small: int
    truffle_counts: list[int]


def select_truffles(
    candidate: RankingCandidate,
    spec: QualificationSpec,
    table: pa.Table,
    label: str,
    utility_sign: int,
) -> Selection:
    """Observations for Evaluator v1, selected exactly as live Sniffer
    qualifies: per frame, the candidate's eligible universe (every model
    feature finite, at least MIN_CROSS_SECTION coins), then every spec
    condition over that universe. `eligible` = in the universe with the
    label present; `outcome` = utility_sign * label. Labels are never
    consulted for selection."""
    frame_times = table.column("frame_time").to_pylist()
    symbols = table.column("symbol").to_pylist()
    names = list(dict.fromkeys(candidate.features + spec.features))
    values = {f: table.column(f).to_pylist() for f in names}
    labels = table.column(label).to_pylist()
    n = len(frame_times)
    selected, eligible = [False] * n, [False] * n
    outcome: list[float | None] = [None] * n
    timestamps = too_small = 0
    counts: list[int] = []
    start = 0
    while start < n:
        end = start
        while end < n and frame_times[end] == frame_times[start]:
            end += 1
        timestamps += 1
        rows = list(range(start, end))
        universe = [
            rows[i]
            for i in eligible_indices(
                {f: [values[f][r] for r in rows] for f in candidate.features}, candidate.features
            )
        ]
        if len(universe) < MIN_CROSS_SECTION:
            too_small += 1
            start = end
            continue
        checks = qual.evaluate(
            spec,
            [str(r) for r in universe],
            {f: [values[f][r] for r in universe] for f in spec.features},
        )
        count = 0
        for r in universe:
            if qualified(checks[str(r)]):
                selected[r] = True
                count += 1
            if labels[r] is not None and math.isfinite(labels[r]):
                eligible[r] = True
                outcome[r] = utility_sign * float(labels[r])
        counts.append(count)
        start = end
    return Selection(
        Observations(frame_times, symbols, selected, eligible, outcome, too_small),
        timestamps,
        too_small,
        counts,
    )


def timestamp_spreads(
    observations: Observations, *, against: Literal["universe", "non_truffles"]
) -> dict[date, list[float]]:
    """Per day, each timestamp's mean Truffle utility minus the mean of the
    reference (eligible universe, or eligible non-Truffles); timestamps
    without a Truffle (or an empty reference) contribute nothing."""
    by_time: dict[datetime, list[int]] = {}
    for i, t in enumerate(observations.frame_time):
        by_time.setdefault(t, []).append(i)
    spreads: dict[date, list[float]] = {}
    for t, rows in sorted(by_time.items()):
        # Eligible rows always carry an outcome.
        sel = [
            v
            for i in rows
            if observations.selected[i] and (v := observations.outcome[i]) is not None
        ]
        ref = [
            v
            for i in rows
            if (v := observations.outcome[i]) is not None
            and (against == "universe" or not observations.selected[i])
        ]
        if sel and ref:
            spreads.setdefault(t.date(), []).append(
                math.fsum(sel) / len(sel) - math.fsum(ref) / len(ref)
            )
    return spreads


def decide(report: dict[str, Any], adjusted: tuple[float, float] | None) -> tuple[Decision, str]:
    verdict = report["verdict"]
    if verdict == "insufficient_data":
        return "insufficient_evidence", "; ".join(report["verdict_reasons"])
    if adjusted is None:
        return "inconclusive", "no adjusted interval"
    low, high = adjusted
    if verdict == "positive_evidence" and low > 0:
        return "consistent_with_improvement", "directional verdict survives the adjustment"
    if verdict == "negative_evidence" and high < 0:
        return "inconsistent_with_improvement", "directional verdict survives the adjustment"
    if verdict in ("positive_evidence", "negative_evidence"):
        return "inconclusive", "directional verdict does not survive the multiplicity adjustment"
    return "inconclusive", "; ".join(report["verdict_reasons"])


def _filter_partition(table: pa.Table, partition: Partition, horizon_minutes: int) -> pa.Table:
    start = day_start(partition.start)
    last = day_start(partition.end) - timedelta(minutes=horizon_minutes)
    times = table.column("frame_time")
    return table.filter(
        pc.and_(
            pc.greater_equal(times, pa.scalar(start, type=times.type)),
            pc.less_equal(times, pa.scalar(last, type=times.type)),
        )
    )


def _seed(r_hash: str, hypothesis_id: str, label: str) -> int:
    return int(_canonical_hash([r_hash, hypothesis_id, label])[:16], 16)


def analyze_claim(
    claim: TruffleClaim,
    candidate: RankingCandidate,
    spec: QualificationSpec,
    tables: dict[str, tuple[pa.Table, int]],
    r_hash: str,
) -> dict[str, Any]:
    """The frozen analysis of one claim on already-loaded, partition-filtered
    tables ({label: (table, horizon_minutes)})."""
    out: dict[str, Any] = {}
    for label, (table, horizon) in tables.items():
        sel = select_truffles(candidate, spec, table, label, claim.utility_sign)
        base = plan_split(sel.observations.frame_time)
        split = evaluation.SplitPlan(
            base.qualifying_days, base.excluded_days, [], base.qualifying_days, None
        )
        seed = _seed(r_hash, claim.hypothesis_id, label)
        report = evaluation.evaluate(
            sel.observations,
            segment="validation",
            split=split,
            horizon_minutes=horizon,
            sample_interval_minutes=15,
            seed=seed,
            validation_reuse={"independent": None, "note": "see the run's independence"},
        )
        days = set(base.qualifying_days)
        universe = {
            d: v
            for d, v in timestamp_spreads(sel.observations, against="universe").items()
            if d in days
        }
        adjusted = (
            evaluation._day_block_interval(universe, seed, ADJUSTED_CONFIDENCE)
            if universe and not report["uncertainty"].get("suppressed")
            else None
        )
        entry: dict[str, Any] = {
            "label": label,
            "report": report,
            "adjusted_interval": list(adjusted) if adjusted else None,
            "adjusted_confidence": ADJUSTED_CONFIDENCE,
            "qualification_frequency": {
                "timestamps": sel.timestamps,
                "timestamps_universe_too_small": sel.timestamps_universe_too_small,
                "timestamps_evaluated": len(sel.truffle_counts),
                "timestamps_with_truffles": sum(1 for c in sel.truffle_counts if c),
                "mean_truffles_per_timestamp": statistics.fmean(sel.truffle_counts)
                if sel.truffle_counts
                else None,
                "median_truffles_per_timestamp": statistics.median(sel.truffle_counts)
                if sel.truffle_counts
                else None,
            },
        }
        if label == claim.primary_label:
            decision, reason = decide(report, adjusted)
            entry["role"] = "primary"
            out["decision"], out["decision_reason"] = decision, reason
            non = {
                d: v
                for d, v in timestamp_spreads(sel.observations, against="non_truffles").items()
                if d in days
            }
            flat = [s for _, v in sorted(non.items()) for s in v]
            entry["versus_non_truffles"] = {
                "role": "secondary",
                "timestamps": len(flat),
                "mean_spread": math.fsum(flat) / len(flat) if flat else None,
                "interval": list(evaluation._day_block_interval(non, seed))
                if len(non) >= evaluation.MIN_DAYS_FOR_INFERENCE
                else None,
                "confidence": CONFIDENCE_LEVEL,
                "note": "secondary, no verdict",
            }
        else:
            entry["role"] = "secondary"
            entry["note"] = "secondary horizon, no decision"
        out.setdefault("analyses", []).append(entry)
    return out


# -- independence and evaluation ------------------------------------------------------------


def _independence(
    data_dir: Path,
    loaded: LoadedTruffle,
    role: str,
    manifests: list[dict[str, Any]],
    before_seq: int,
) -> tuple[str, dict[str, Any]]:
    reg = loaded.registration
    own_prior = any(
        a["outcome"] not in ("authorized_only", "failed_before_exposure")
        and min(e["seq"] for e in a["events"]) < before_seq
        for a in attempts(loaded.events, role)
    )
    partition = next(p for p in reg.partitions if p.role == role)
    overlapping = []
    for directory in registration_dirs(data_dir):
        stored = read_registration_file(directory)["registration"]
        if stored.get("registration_id") == reg.registration_id:
            continue
        events = read_events(directory)
        for p in stored.get("partitions", []):
            if (
                date.fromisoformat(p["start"]) < partition.end
                and partition.start < date.fromisoformat(p["end"])
                and exposed(events, p["role"])
            ):
                overlapping.append(f"{stored['registration_id']}/{p['role']}")
    late = [
        m.get("shard_day")
        for m in manifests
        if not m.get("generated_at")
        or datetime.fromisoformat(m["generated_at"]) <= reg.registered_at
    ]
    details = {
        "prior_exposure_same_registration": own_prior,
        "overlapping_exposures_other_registrations": overlapping,
        "shards_not_generated_after_registration": late,
    }
    if own_prior:
        return "repeat_exposure", details
    if late or not manifests:
        return "undetermined", details
    return "independent_prospective", details


def evaluate_truffles(
    data_dir: Path,
    candidates_dir: Path,
    qualifications_dir: Path,
    registration_id: str,
    role: Role,
    *,
    now: datetime | None = None,
    protected_holdout: bool = False,
    acknowledge_prior_exposure: bool = False,
) -> dict[str, Any]:
    """Run one eligible evaluation. Refuses before eligibility, on any
    verification problem, and for the holdout without `protected_holdout`
    or once exposed. Records `authorized`, then `started` before any label
    is read, then `completed` or `failed`."""
    now = now or datetime.now(UTC)
    loaded = load_truffle(data_dir, registration_id)
    problems = verify_truffle_registration(loaded, candidates_dir, qualifications_dir)
    if problems:
        raise ProtocolError("registration does not verify: " + "; ".join(problems))
    reg = loaded.registration
    partition = next((p for p in reg.partitions if p.role == role), None)
    if partition is None:
        raise ProtocolError(f"registration {registration_id} has no {role} partition")
    if role == "holdout" and not protected_holdout:
        raise ProtocolError("the protected holdout needs an explicit --protected-holdout")
    state = registration_partition_state(reg, loaded.events, role, data_dir, now)
    if state["state"] == "awaiting_data":
        raise ProtocolError(f"{role} is not eligible until {state['eligible_from']}")
    if exposed(loaded.events, role) and (role == "holdout" or not acknowledge_prior_exposure):
        raise ProtocolError(
            f"{role} was already exposed for this registration"
            + (
                "; the protected holdout cannot be re-run"
                if role == "holdout"
                else "; re-running yields non-independent evidence "
                "(pass --acknowledge-prior-exposure)"
            )
        )

    if role == "holdout":
        _, prior = _independence(data_dir, loaded, role, [], 1)
        if prior["overlapping_exposures_other_registrations"]:
            raise ProtocolError(
                "the holdout period was already exposed by "
                + ", ".join(prior["overlapping_exposures_other_registrations"])
            )

    shards = []
    incompatible = []
    for d, m in partition_shards(data_dir, partition):
        if m is None:
            continue
        if m.get("temporal_contract_version") != reg.plan.temporal_contract_version:
            incompatible.append(d.isoformat())
            continue
        shards.append((d, m))
    if not shards:
        raise ProtocolError(f"no usable shard exists for any day of the {role} partition")
    datasets = [DatasetRef(name=shard_name(d), data_sha256=m["data_sha256"]) for d, m in shards]
    attempt_id = uuid4().hex[:12]
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
            "incompatible_days": incompatible,
        },
    )
    kind, details = _independence(data_dir, loaded, role, [m for _, m in shards], authorized["seq"])
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
    r_hash = registration_hash(reg)
    try:
        for claim in reg.claims:
            candidate, spec = _load_specs(
                candidates_dir, qualifications_dir, claim.ranking_candidate, claim.qualification_id
            )
            features = list(dict.fromkeys(candidate.features + spec.features))
            tables = {}
            for label in [claim.primary_label, *claim.secondary_labels]:
                loaded_data = load_columns(datasets, features, label, data_dir, access=access)
                tables[label] = (
                    _filter_partition(loaded_data.table, partition, loaded_data.horizon_minutes),
                    loaded_data.horizon_minutes,
                )
            analysis = analyze_claim(claim, candidate, spec, tables, r_hash)
            experiment_id = f"{registration_id}--{claim.hypothesis_id.lower()}"
            definition = {"registration_hash": r_hash, "role": role, "claim": claim.model_dump()}
            runs_root = data_dir / RUNS_DIRNAME
            check_experiment_identity(
                load_runs(runs_root), experiment_id, _canonical_hash(definition)
            )
            executed_at = datetime.now(UTC)
            record = {
                "run_id": new_run_id(executed_at),
                "executed_at": executed_at.isoformat(),
                "kind": "prospective_truffle_evaluation",
                "experiment_id": experiment_id,
                "spec_hash": _canonical_hash(definition),
                "registration_id": registration_id,
                "registration_hash": r_hash,
                "hypothesis_id": claim.hypothesis_id,
                "direction": claim.direction,
                "ranking_candidate": claim.ranking_candidate,
                "ranking_spec_hash": claim.ranking_spec_hash,
                "qualification_id": claim.qualification_id,
                "qualification_spec_hash": claim.qualification_spec_hash,
                "role": role,
                "segment": role,
                "partition": {
                    "start": partition.start.isoformat(),
                    "end": partition.end.isoformat(),
                },
                "attempt_id": attempt_id,
                "independence": kind,
                "independence_details": details,
                "label": claim.primary_label,
                "evaluator_version": EVALUATOR_VERSION,
                "truffle_protocol_version": TRUFFLE_PROTOCOL_VERSION,
                "code_fingerprint": code_fingerprint(),
                "git_commit": get_git_commit(),
                "datasets": [
                    {
                        "name": d.name,
                        "data_sha256": d.data_sha256,
                        "generated_at": m.get("generated_at"),
                    }
                    for d, (_, m) in zip(datasets, shards, strict=True)
                ],
                "decision": analysis["decision"],
                "decision_reason": analysis["decision_reason"],
                "analyses": analysis["analyses"],
                "limitations": list(reg.limitations),
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
        },
    )


def verify_truffle_run(data_dir: Path, record: dict[str, Any]) -> list[str]:
    """Is a Truffle run backed by its registration's exposure log? (Used by
    model knowledge before counting it, like
    `app.research.prospective.verify_prospective_run`.)"""
    try:
        loaded = load_truffle(data_dir, str(record.get("registration_id")))
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
    elif f"{record.get('experiment_id')}/{record.get('run_id')}" not in completed[0].get(
        "runs", []
    ):
        problems.append(f"{record.get('run_id')}: not listed by its completed event")
    elif completed[0].get("independence") != record.get("independence"):
        problems.append(f"{record.get('run_id')}: independence differs from the exposure log")
    return problems
