"""Prospective Truffle evaluation (app.research.truffle_evaluation):
registration, protection, eligibility, selection parity with live Sniffer,
statistics and decisions. Synthetic shards exercise mechanics only — never
evidence about markets."""

import json
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from research_helpers import write_dataset

from app.ranking.qualification import load_qualification
from app.ranking.scoring import load_candidate_file
from app.research import prospective
from app.research import truffle_evaluation as te
from app.research.continuity import shard_name
from app.research.experiment import DatasetRef, ExperimentSpecError, load_columns
from app.research.prospective import Partition
from app.research.reservations import ReservedDataError, check_generation_window
from app.research.runs import load_runs
from app.services.live_rankings import _qualify, rank_direction

REPO = Path(__file__).resolve().parents[3]
RANKING = REPO / "research/oink_corp/ranking"
EXISTING = REPO / "research/oink_corp/data/evaluations/oi-price-1h-prospective-v1"
PAIRS = [
    ("long-price-reversal-oi-v1", "long-reversal-oi-truffle-v1"),
    ("short-price-reversal-oi-v1", "short-reversal-oi-truffle-v1"),
]
REGISTERED = datetime(2026, 9, 20, 12, tzinfo=UTC)
VALIDATION = Partition(role="validation", start=date(2026, 9, 22), end=date(2026, 10, 2))
HOLDOUT = Partition(role="holdout", start=date(2026, 10, 3), end=date(2026, 10, 13))
AFTER = datetime(2026, 11, 1, tzinfo=UTC)
TIMESTAMPS = 20
N = 60


def _frame_rows(t: datetime, rotation: int, *, long_bonus: float, short_bonus: float, zero=False):
    """60 coins: positions 0-2 are Long Truffles (weakest, falling, OI among
    the lowest and falling), 57-59 Short Truffles (strongest, rising, OI
    falling). Labels: Long Truffles +long_bonus, Short Truffles -short_bonus
    (good for a Short), everyone else 0. `zero`: everything rose, no Long
    Truffle can qualify and the Short side loses its falling OI."""
    rows = []
    for position in range(N):
        symbol = f"S{(position + rotation) % N:03d}"
        ret = 0.05 + position * 0.001 if zero else -0.03 + position * 0.001
        oi_rank = {0: 0, 1: 1, 2: 2, 57: 3, 58: 4, 59: 5}.get(position, 6 + position)
        oi = 0.01 + oi_rank * 0.001 if zero else -0.02 + oi_rank * 0.0005
        label = 0.0
        if position < 3:
            label = long_bonus
        elif position >= 57:
            label = -short_bonus
        rows.append(
            {
                "frame_time": t,
                "symbol": symbol,
                "return_1h": ret,
                "oi_change_1h": oi,
                "forward_relative_return_1h": label,
                "forward_relative_return_4h": label,
            }
        )
    return rows


def _shard(
    data: Path,
    day: date,
    *,
    long_bonus=0.01,
    short_bonus=0.01,
    generated_at=None,
    contract=2,
    zero_frames=0,
    missing_label_for=None,
):
    rows = []
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    for i in range(TIMESTAMPS):
        t = start + timedelta(minutes=15 * i)
        frame = _frame_rows(
            t,
            i + day.toordinal(),
            long_bonus=long_bonus,
            short_bonus=short_bonus,
            zero=i < zero_frames,
        )
        if missing_label_for is not None and i == 0:
            frame[missing_label_for]["forward_relative_return_1h"] = None
        rows += frame
    path = data / shard_name(day)
    write_dataset(path, rows, sample_interval_minutes=15, horizons_minutes={"1h": 60, "4h": 240})
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["generated_at"] = (
        generated_at or datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    ).isoformat()
    manifest["shard_day"] = day.isoformat()
    if contract is not None:
        manifest["temporal_contract_version"] = contract
    (path / "manifest.json").write_text(json.dumps(manifest))


def _shards(data, partition, **kwargs):
    day = partition.start
    while day < partition.end:
        _shard(data, day, **kwargs)
        day += timedelta(days=1)


@pytest.fixture
def lab(tmp_path):
    ranking = tmp_path / "ranking"
    shutil.copytree(RANKING, ranking)
    data = tmp_path / "data"
    data.mkdir()
    return {"data": data, "ranking": ranking}


def _build(lab, rid="truffle-test-v1", partitions=(VALIDATION, HOLDOUT), at=REGISTERED):
    return te.build_truffle_registration(
        lab["ranking"] / "candidates",
        lab["ranking"] / "qualifications",
        registration_id=rid,
        partitions=list(partitions),
        registered_at=at,
        pairs=PAIRS,
    )


def _register(lab, **kwargs):
    registration = _build(lab, **kwargs)
    te.register_truffles(lab["data"], registration)
    return registration


def _evaluate(lab, role="validation", rid="truffle-test-v1", **kwargs):
    return te.evaluate_truffles(
        lab["data"],
        lab["ranking"] / "candidates",
        lab["ranking"] / "qualifications",
        rid,
        role,
        now=kwargs.pop("now", AFTER),
        **kwargs,
    )


def _runs(lab):
    return [
        r
        for r in load_runs(lab["data"] / "runs").records
        if r.get("kind") == "prospective_truffle_evaluation"
    ]


# -- registration ---------------------------------------------------------------------------


def test_the_registration_freezes_exact_hashes_and_independent_directional_claims(lab):
    registration = _build(lab)
    long, short = registration.claims
    candidates, quals = lab["ranking"] / "candidates", lab["ranking"] / "qualifications"
    from app.ranking.qualification import qualification_hash
    from app.ranking.scoring import candidate_hash

    assert (long.hypothesis_id, long.direction, long.utility_sign) == ("T-LONG", "long", 1)
    assert (short.hypothesis_id, short.direction, short.utility_sign) == ("T-SHORT", "short", -1)
    for claim, (cid, qid) in zip(registration.claims, PAIRS, strict=True):
        assert claim.ranking_spec_hash == candidate_hash(
            load_candidate_file(candidates / f"{cid}.json")
        )
        assert claim.qualification_spec_hash == qualification_hash(load_qualification(quals, qid))
        assert claim.prediction_direction == "positive"
        assert claim.primary_label == "forward_relative_return_1h"
        assert claim.secondary_labels == ["forward_relative_return_4h"]
    plan = registration.plan
    assert plan.temporal_contract_version == 2 and plan.min_cross_section == 50
    assert plan.multiplicity["family_size"] == 2
    assert plan.multiplicity["adjusted_confidence"] == pytest.approx(0.95)
    assert set(plan.decision_rules) == {
        "insufficient_evidence",
        "consistent_with_improvement",
        "inconsistent_with_improvement",
        "inconclusive",
    }
    assert (
        "zero" not in plan.primary_reference and "includes the Truffles" in plan.primary_reference
    )
    assert "no qualified-group return is fabricated" in plan.zero_truffle_frames
    assert "not an achievable entry return" in registration.limitations[0]


def test_serialization_and_hash_are_deterministic(lab):
    a, b = _build(lab), _build(lab)
    assert prospective.registration_hash(a) == prospective.registration_hash(b)
    path = te.register_truffles(lab["data"], a)
    stored = json.loads((path / "registration" / "registration.json").read_text())
    assert stored["registration_hash"] == prospective.registration_hash(a)
    assert stored["registration"]["kind"] == "truffle_qualification"
    with pytest.raises(prospective.ProspectiveError, match="already exists"):
        te.register_truffles(lab["data"], b)


def test_a_window_must_start_after_registration_plus_embargo(lab):
    early = Partition(role="validation", start=date(2026, 9, 20), end=date(2026, 9, 30))
    with pytest.raises(prospective.ProspectiveError, match="must start on or after"):
        _register(lab, partitions=(early,))


def test_a_window_touching_another_registration_is_refused(lab):
    """Including the real oi-price-1h-prospective-v1: its validation and
    holdout (2026-10-26 to 2026-12-26) cannot be shared or bordered."""
    shutil.copytree(EXISTING, lab["data"] / "evaluations" / EXISTING.name)
    at = datetime(2026, 10, 25, 1, 42, tzinfo=UTC)
    inside = Partition(role="validation", start=date(2026, 11, 1), end=date(2026, 11, 10))
    bordering = Partition(role="validation", start=date(2026, 12, 26), end=date(2027, 1, 20))
    for partition in (inside, bordering):
        with pytest.raises(te.ProtocolError, match="oi-price-1h-prospective-v1"):
            _register(lab, rid="truffle-x-v1", partitions=(partition,), at=at)
    after = (
        Partition(role="validation", start=date(2026, 12, 27), end=date(2027, 1, 26)),
        Partition(role="holdout", start=date(2027, 1, 27), end=date(2027, 2, 26)),
    )
    _register(lab, rid="truffle-ok-v1", partitions=after, at=at)
    # The existing registration is untouched and still loads as a campaign registration.
    assert [x.registration.registration_id for x in prospective.load_all(lab["data"])] == [
        "oi-price-1h-prospective-v1"
    ]
    with pytest.raises(prospective.ProspectiveError, match="not a campaign-claim"):
        prospective.load(lab["data"], "truffle-ok-v1")


# -- protection and eligibility -------------------------------------------------------------


def test_reserved_truffle_days_are_refused_to_every_other_loader(lab):
    _register(lab)
    _shard(lab["data"], VALIDATION.start)
    manifest = json.loads(
        (lab["data"] / shard_name(VALIDATION.start) / "manifest.json").read_text()
    )
    ref = DatasetRef(name=shard_name(VALIDATION.start), data_sha256=manifest["data_sha256"])
    with pytest.raises(ExperimentSpecError, match="truffle-test-v1"):
        load_columns([ref], ["return_1h"], "forward_relative_return_1h", lab["data"])
    with pytest.raises(ReservedDataError):
        check_generation_window(
            lab["data"], datetime(2026, 9, 25, tzinfo=UTC), datetime(2026, 9, 26, tzinfo=UTC)
        )


def test_evaluation_is_refused_before_eligibility_and_reads_nothing(lab):
    _register(lab)
    _shards(lab["data"], VALIDATION)
    with pytest.raises(te.ProtocolError, match="not eligible until 2026-10-02"):
        _evaluate(lab, now=datetime(2026, 10, 1, tzinfo=UTC))
    assert te.load_truffle(lab["data"], "truffle-test-v1").events == []
    status = te.truffle_status(
        te.load_truffle(lab["data"], "truffle-test-v1"),
        lab["data"],
        datetime(2026, 10, 1, tzinfo=UTC),
    )
    assert status["partitions"][0]["lifecycle"] == "registered_awaiting_data"


def test_a_changed_specification_does_not_inherit_the_registration(lab):
    _register(lab)
    _shards(lab["data"], VALIDATION)
    path = lab["ranking"] / "qualifications" / "long-reversal-oi-truffle-v1.json"
    raw = json.loads(path.read_text())
    raw["conditions"][0]["at_most"] = 0.1
    path.write_text(json.dumps(raw))
    loaded = te.load_truffle(lab["data"], "truffle-test-v1")
    problems = te.verify_truffle_registration(
        loaded, lab["ranking"] / "candidates", lab["ranking"] / "qualifications"
    )
    assert any("does not inherit" in p for p in problems)
    with pytest.raises(te.ProtocolError, match="does not verify"):
        _evaluate(lab)
    assert te.load_truffle(lab["data"], "truffle-test-v1").events == []


# -- evaluation -----------------------------------------------------------------------------


def test_an_eligible_evaluation_records_exposure_and_decides_per_direction(lab):
    _register(lab)
    _shards(lab["data"], VALIDATION)
    event = _evaluate(lab)
    loaded = te.load_truffle(lab["data"], "truffle-test-v1")
    assert [e["type"] for e in loaded.events] == ["authorized", "started", "completed"]
    assert event["independence"] == "independent_prospective"
    runs = {r["hypothesis_id"]: r for r in _runs(lab)}
    for hid in ("T-LONG", "T-SHORT"):
        run = runs[hid]
        assert run["decision"] == "consistent_with_improvement"
        primary = run["analyses"][0]
        assert primary["role"] == "primary" and primary["label"] == "forward_relative_return_1h"
        assert primary["report"]["verdict"] == "positive_evidence"
        assert primary["adjusted_interval"][0] > 0
        # Short utility is the negated label: Short Truffles' -0.01 labels count +0.01.
        assert primary["report"]["outcomes"]["selected_mean"] == pytest.approx(0.01)
        assert primary["qualification_frequency"]["timestamps_with_truffles"] == 200
        assert primary["qualification_frequency"]["mean_truffles_per_timestamp"] == 3
        secondary = run["analyses"][1]
        assert secondary["role"] == "secondary" and "decision" not in secondary
        assert primary["versus_non_truffles"]["role"] == "secondary"
        assert te.verify_truffle_run(lab["data"], run) == []
    status = te.truffle_status(loaded, lab["data"], AFTER)
    assert status["partitions"][0]["lifecycle"] == "evaluated"
    assert status["partitions"][0]["decisions"] == {
        "T-LONG": "consistent_with_improvement",
        "T-SHORT": "consistent_with_improvement",
    }
    with pytest.raises(te.ProtocolError, match="already exposed"):
        _evaluate(lab)
    with pytest.raises(te.ProtocolError, match="--protected-holdout"):
        _evaluate(lab, role="holdout")


def test_evidence_against_improvement_is_reported_as_such(lab):
    _register(lab)
    _shards(lab["data"], VALIDATION, long_bonus=-0.01, short_bonus=-0.01)
    _evaluate(lab)
    assert {r["decision"] for r in _runs(lab)} == {"inconsistent_with_improvement"}


def test_too_little_data_is_insufficient_evidence(lab):
    _register(lab)
    _shard(lab["data"], VALIDATION.start)  # one day of ten
    _evaluate(lab, now=datetime(2026, 10, 5, tzinfo=UTC))
    assert {r["decision"] for r in _runs(lab)} == {"insufficient_evidence"}


def test_zero_truffle_frames_count_in_coverage_but_add_no_spread(lab):
    _register(lab)
    _shards(lab["data"], VALIDATION, zero_frames=5)
    _evaluate(lab)
    long = next(r for r in _runs(lab) if r["hypothesis_id"] == "T-LONG")
    primary = long["analyses"][0]
    counts = primary["report"]["counts"]
    assert counts["timestamps"] == 200 and counts["timestamps_without_selection"] == 50
    assert counts["timestamps_with_selection"] == 150
    assert primary["qualification_frequency"]["timestamps_with_truffles"] == 150
    # Long (+0.01) and Short (-0.01) Truffles cancel in the universe mean.
    assert primary["report"]["spread"]["mean"] == pytest.approx(0.01)


def test_a_truffle_without_an_outcome_is_counted_not_filled(lab):
    _register(lab)
    _shards(lab["data"], VALIDATION, missing_label_for=0)
    _evaluate(lab)
    long = next(r for r in _runs(lab) if r["hypothesis_id"] == "T-LONG")
    counts = long["analyses"][0]["report"]["counts"]
    assert counts["selected_missing_outcome"] == 10  # one per day


def test_shards_from_another_temporal_contract_are_not_evaluated(lab):
    _register(lab)
    _shards(lab["data"], VALIDATION, contract=None)
    with pytest.raises(te.ProtocolError, match="no usable shard"):
        _evaluate(lab)
    assert te.load_truffle(lab["data"], "truffle-test-v1").events == []


def test_the_multiplicity_adjustment_can_downgrade_a_verdict():
    report = {"verdict": "positive_evidence", "verdict_reasons": []}
    assert te.decide(report, (0.001, 0.02))[0] == "consistent_with_improvement"
    decision, reason = te.decide(report, (-0.001, 0.02))
    assert decision == "inconclusive" and "multiplicity" in reason
    assert te.decide({"verdict": "insufficient_data", "verdict_reasons": ["few"]}, None)[0] == (
        "insufficient_evidence"
    )


# -- parity with live qualification --------------------------------------------------------


def test_evaluated_truffles_are_exactly_the_live_truffles_of_the_same_frame(lab, tmp_path):
    rows = _frame_rows(datetime(2026, 9, 22, tzinfo=UTC), 7, long_bonus=0.0, short_bonus=0.0)
    rows[10]["oi_change_1h"] = None  # outside the ranked universe, live and here
    import pyarrow as pa

    table = pa.table(
        {
            "frame_time": [r["frame_time"] for r in rows],
            "symbol": [r["symbol"] for r in rows],
            "return_1h": [r["return_1h"] for r in rows],
            "oi_change_1h": [r["oi_change_1h"] for r in rows],
            "forward_relative_return_1h": [0.0] * len(rows),
        }
    )
    symbols = [r["symbol"] for r in rows]
    features = {f: [r[f] for r in rows] for f in ("return_1h", "oi_change_1h")}
    for cid, qid in PAIRS:
        candidate = load_candidate_file(lab["ranking"] / "candidates" / f"{cid}.json")
        spec = load_qualification(lab["ranking"] / "qualifications", qid)
        sel = te.select_truffles(candidate, spec, table, "forward_relative_return_1h", 1)
        evaluated = {
            s
            for s, chosen in zip(sel.observations.symbol, sel.observations.selected, strict=True)
            if chosen
        }
        ranking = rank_direction(
            cid, candidate.direction, lab["ranking"] / "candidates", symbols, features
        )
        live, _ = _qualify(ranking, lab["ranking"] / "qualifications", qid, symbols, features)
        # One coin left the universe, so the 5% tail now holds 2 coins: both agree.
        assert evaluated == {t["symbol"] for t in live["truffles"]} and evaluated
