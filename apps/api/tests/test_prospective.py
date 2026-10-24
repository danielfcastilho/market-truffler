"""Prospective evaluation protocol — synthetic data only (no real holdout is
ever read)."""

import json
from datetime import UTC, date, datetime, timedelta

import pyarrow as pa
import pytest
from research_helpers import write_dataset
from test_campaign import LABEL, _campaign, _rows

from app.research import prospective
from app.research.campaign import campaign_hash, load_campaign
from app.research.continuity import shard_name
from app.research.experiment import ExperimentSpecError, load_datasets
from app.research.knowledge import derive_ledger, verify_revision
from app.research.prospective import (
    Partition,
    ProspectiveError,
    append_event,
    build_registration,
    evaluate,
    load,
    partition_state,
    register,
    verify_prospective_run,
    verify_registration,
)
from app.research.reservations import Access, ReservedDataError, check_access
from app.research.runs import load_runs
from app.scripts import run_experiment
from app.scripts.run_campaign import run_comparison

REGISTERED = datetime(2026, 9, 20, 12, tzinfo=UTC)
VALIDATION = Partition(role="validation", start=date(2026, 9, 22), end=date(2026, 10, 6))
HOLDOUT = Partition(role="holdout", start=date(2026, 10, 7), end=date(2026, 10, 21))
AFTER = datetime(2026, 11, 1, tzinfo=UTC)


def _day_rows(day: date, bonus: float):
    """One synthetic day like test_campaign._rows: rotating ranks, OI falling
    for odd ranks, outcome raised by `bonus` where OI fell."""
    rows = []
    for i in range(20):
        t = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(minutes=15 * i)
        for k in range(60):
            rank = (k + i + day.toordinal()) % 60
            oi = -0.02 if rank % 2 else 0.03
            rows.append(
                {
                    "frame_time": t,
                    "symbol": f"S{k:03d}",
                    "return_1h": float(rank),
                    "oi_change_1h": oi,
                    LABEL: bonus if oi < 0 else 0.0,
                }
            )
    return rows


def _shard(data, day: date, *, bonus=0.01, generated_at=None, name=None):
    path = data / (name or shard_name(day))
    write_dataset(path, _day_rows(day, bonus), sample_interval_minutes=15)
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["generated_at"] = (
        generated_at or datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    ).isoformat()
    manifest["shard_day"] = day.isoformat()
    (path / "manifest.json").write_text(json.dumps(manifest))
    return path


def _shards(data, partition: Partition, **kwargs):
    day = partition.start
    while day < partition.end:
        _shard(data, day, **kwargs)
        day += timedelta(days=1)


@pytest.fixture
def lab(tmp_path):
    """A campaign (H1 vs price-only), its development runs on September
    1-2, and the data and experiment directories."""
    path, experiments, data = _campaign(tmp_path, _rows(2))
    registered = json.loads(path.read_text())
    registered["hypotheses"][0]["prediction"] = "Positive incremental spread."
    path.write_text(json.dumps(registered))
    campaign, specs = load_campaign(path, experiments)
    for experiment_id in campaign.experiment_ids:
        run_experiment.run(experiments / f"{experiment_id}.json", "development", data)
    run_comparison(
        campaign,
        campaign_hash(campaign, specs),
        campaign.comparisons[0],
        specs,
        "development",
        data,
    )
    return {
        "path": path,
        "experiments": experiments,
        "data": data,
        "campaign": campaign,
        "specs": specs,
    }


def _registration(
    lab, rid="reg-a-v1", partitions=(VALIDATION, HOLDOUT), at=REGISTERED, supersedes=None
):
    return build_registration(
        lab["campaign"],
        lab["specs"],
        registration_id=rid,
        hypothesis_ids=["H1"],
        partitions=list(partitions),
        registered_at=at,
        supersedes=supersedes,
    )


def _register(lab, **kwargs):
    registration = _registration(lab, **kwargs)
    register(lab["data"], registration)
    return registration


def _evaluate(lab, rid="reg-a-v1", role="validation", **kwargs):
    return evaluate(lab["data"], lab["campaign"], lab["specs"], rid, role, now=AFTER, **kwargs)


# -- registration ---------------------------------------------------------------------------


def test_a_registration_freezes_claims_and_is_written_once(lab):
    registration = _register(lab)
    loaded = load(lab["data"], "reg-a-v1")
    assert loaded.registration == registration
    assert verify_registration(loaded, lab["campaign"], lab["specs"]) == []
    [claim] = registration.claims
    assert claim.comparison_id == "h1-vs-price-only-v1" and claim.prediction.startswith("Positive")
    assert registration.plan.minimum_evidence["MIN_DAYS_FOR_INFERENCE"] == 10
    with pytest.raises(ProspectiveError, match="already exists"):
        register(lab["data"], registration)


def test_editing_a_registration_or_its_claims_breaks_verification(lab):
    _register(lab)
    path = lab["data"] / "evaluations" / "reg-a-v1" / "registration" / "registration.json"
    stored = json.loads(path.read_text())
    stored["registration"]["partitions"][0]["end"] = "2026-10-05"
    path.write_text(json.dumps(stored))
    problems = verify_registration(load(lab["data"], "reg-a-v1"), lab["campaign"], lab["specs"])
    assert any("was modified" in p for p in problems)


def test_a_changed_spec_requires_a_new_registration(lab):
    _register(lab)
    spec_path = lab["experiments"] / "h1-long-v1.json"
    spec = json.loads(spec_path.read_text())
    spec["hypothesis"]["conditions"][1]["value"] = -0.01
    spec_path.write_text(json.dumps(spec))
    campaign, specs = load_campaign(lab["path"], lab["experiments"])
    problems = verify_registration(load(lab["data"], "reg-a-v1"), campaign, specs)
    assert any("campaign or its specs changed" in p for p in problems)
    assert any("specification changed" in p for p in problems)
    with pytest.raises(ProspectiveError, match="does not verify"):
        evaluate(lab["data"], campaign, specs, "reg-a-v1", "validation", now=AFTER)


@pytest.mark.parametrize(
    ("partitions", "message"),
    [
        # Registered at 12:00 + 4h embargo: the 20th is too early.
        (
            [Partition(role="validation", start=date(2026, 9, 20), end=date(2026, 9, 25))],
            "must start on or after",
        ),
        (
            [Partition(role="validation", start=date(2026, 9, 22), end=date(2026, 9, 22))],
            "end must be after",
        ),
        (
            [VALIDATION, Partition(role="holdout", start=date(2026, 10, 6), end=date(2026, 10, 9))],
            "embargo",
        ),
        ([HOLDOUT, VALIDATION], "one validation, optionally followed"),
    ],
)
def test_invalid_partitions_are_refused(lab, partitions, message):
    with pytest.raises(ProspectiveError, match=message):
        register(lab["data"], _registration(lab, partitions=partitions))


def test_registration_after_outcomes_exist_is_refused(lab):
    _shard(lab["data"], date(2026, 9, 30))
    with pytest.raises(ProspectiveError, match="not unseen"):
        register(lab["data"], _registration(lab))
    # Even a day *before* the partition whose labels reach into it.
    (lab["data"] / shard_name(date(2026, 9, 30))).rename(lab["data"] / "moved")
    _shard(lab["data"], date(2026, 9, 21), name="day-before")
    manifest_path = lab["data"] / "day-before" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["last_frame_time"] = "2026-09-21T23:45:00+00:00"
    manifest["horizons_minutes"] = {"1h": 60}
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ProspectiveError, match="day-before"):
        register(lab["data"], _registration(lab))


# -- reservations and temporal integrity -------------------------------------------------------


def _spec_for(lab, *names):
    spec = lab["specs"]["h1-long-v1"]
    refs = [
        {
            "name": n,
            "data_sha256": json.loads((lab["data"] / n / "manifest.json").read_text())[
                "data_sha256"
            ],
        }
        for n in names
    ]
    return spec.model_copy(update={"datasets": [type(spec.datasets[0])(**r) for r in refs]})


def test_reserved_data_cannot_be_read_by_ordinary_research(lab):
    _register(lab)
    _shard(lab["data"], date(2026, 9, 25))
    _shard(lab["data"], date(2026, 9, 25), name="renamed-copy")  # same observations, other name
    for name in (shard_name(date(2026, 9, 25)), "renamed-copy"):
        with pytest.raises(ExperimentSpecError, match="reserved for the validation"):
            load_datasets(_spec_for(lab, name), lab["data"])
    holdout_day = _shard(lab["data"], date(2026, 10, 10)).name
    with pytest.raises(ExperimentSpecError, match="reserved for the holdout"):
        load_datasets(_spec_for(lab, holdout_day), lab["data"])
    # Data outside every partition stays usable for development.
    assert load_datasets(lab["specs"]["h1-long-v1"], lab["data"]).table.num_rows > 0


def test_a_grant_works_only_for_a_live_started_attempt_of_that_partition(lab):
    _register(lab)
    _shard(lab["data"], date(2026, 9, 25))
    manifests = [
        (
            "d",
            json.loads((lab["data"] / shard_name(date(2026, 9, 25)) / "manifest.json").read_text()),
        )
    ]
    fake = Access("reg-a-v1", "validation", "nope")
    with pytest.raises(ReservedDataError):
        check_access(lab["data"], manifests, fake)
    append_event(
        lab["data"], "reg-a-v1", {"type": "started", "attempt_id": "a1", "role": "validation"}
    )
    check_access(lab["data"], manifests, Access("reg-a-v1", "validation", "a1"))
    with pytest.raises(ReservedDataError):  # wrong partition
        check_access(lab["data"], manifests, Access("reg-a-v1", "holdout", "a1"))
    append_event(
        lab["data"], "reg-a-v1", {"type": "failed", "attempt_id": "a1", "role": "validation"}
    )
    with pytest.raises(ReservedDataError):  # attempt over: grant expired
        check_access(lab["data"], manifests, Access("reg-a-v1", "validation", "a1"))


def test_an_unreadable_registration_fails_closed(lab):
    _register(lab)
    (lab["data"] / "evaluations" / "reg-a-v1" / "registration" / "registration.json").write_text(
        "{"
    )
    with pytest.raises(ExperimentSpecError, match="reservations are unknown"):
        load_datasets(lab["specs"]["h1-long-v1"], lab["data"])


def test_no_label_reaches_past_the_partition_end():
    times = [
        datetime(2026, 9, 21, 22, 45, tzinfo=UTC) + timedelta(minutes=15 * i) for i in range(6)
    ]
    table = pa.table({"frame_time": pa.array(times, type=pa.timestamp("us", tz="UTC"))})
    partition = Partition(role="validation", start=date(2026, 9, 22), end=date(2026, 9, 23))
    kept = prospective._filter_partition(table, partition, 60).column("frame_time").to_pylist()
    # 22:45 … 23:45 on the 21st are before the partition; only 00:00 is in.
    assert kept == [datetime(2026, 9, 22, 0, 0, tzinfo=UTC)]
    late = [datetime(2026, 9, 22, 23, m, tzinfo=UTC) for m in (0, 15)]
    late_table = pa.table({"frame_time": pa.array(late, type=pa.timestamp("us", tz="UTC"))})
    assert prospective._filter_partition(late_table, partition, 60).column(
        "frame_time"
    ).to_pylist() == [datetime(2026, 9, 22, 23, 0, tzinfo=UTC)]


def test_future_data_is_not_yet_eligible(lab):
    _register(lab)
    loaded = load(lab["data"], "reg-a-v1")
    assert (
        partition_state(loaded, "validation", lab["data"], REGISTERED)["state"] == "awaiting_data"
    )
    with pytest.raises(ProspectiveError, match="not eligible"):
        evaluate(
            lab["data"], lab["campaign"], lab["specs"], "reg-a-v1", "validation", now=REGISTERED
        )
    # Ended but shards missing: eligible only READY_AFTER later.
    end = datetime(2026, 10, 6, tzinfo=UTC)
    assert (
        partition_state(loaded, "validation", lab["data"], end + timedelta(hours=1))["state"]
        == "awaiting_data"
    )
    assert (
        partition_state(loaded, "validation", lab["data"], end + prospective.READY_AFTER)["state"]
        == "eligible"
    )


# -- evaluation and exposure -------------------------------------------------------------------


def test_a_first_eligible_evaluation_is_independent_and_fully_recorded(lab):
    _register(lab)
    _shards(lab["data"], VALIDATION)
    completed = _evaluate(lab)
    assert completed["independence"] == "independent_prospective"
    loaded = load(lab["data"], "reg-a-v1")
    assert [e["type"] for e in loaded.events] == ["authorized", "started", "completed"]
    [record] = [
        r
        for r in load_runs(lab["data"] / "runs").records
        if r.get("kind") == "prospective_evaluation"
    ]
    assert record["report"]["verdict"] == "positive_evidence"  # Evaluator rules, unchanged
    assert record["role"] == "validation" and record["independence"] == "independent_prospective"
    assert verify_prospective_run(lab["data"], record) == []
    # Completed: the validation reservation is released for development.
    load_datasets(_spec_for(lab, shard_name(date(2026, 9, 25))), lab["data"])


def test_repeated_evaluation_needs_acknowledgement_and_is_never_independent(lab):
    _register(lab)
    _shards(lab["data"], VALIDATION)
    _evaluate(lab)
    with pytest.raises(ProspectiveError, match="already exposed"):
        _evaluate(lab)
    again = _evaluate(lab, acknowledge_prior_exposure=True)
    assert again["independence"] == "repeat_exposure"


def test_a_failed_evaluation_counts_as_exposure(lab, monkeypatch):
    _register(lab)
    _shards(lab["data"], VALIDATION)

    def boom(*args, **kwargs):
        raise RuntimeError("crashed after reading labels")

    monkeypatch.setattr(prospective, "evaluate_incremental", boom)
    with pytest.raises(RuntimeError):
        _evaluate(lab)
    loaded = load(lab["data"], "reg-a-v1")
    assert [e["type"] for e in loaded.events] == ["authorized", "started", "failed"]
    assert partition_state(loaded, "validation", lab["data"], AFTER)["state"] == "failed"
    monkeypatch.undo()
    with pytest.raises(ProspectiveError, match="already exposed"):
        _evaluate(lab)
    assert _evaluate(lab, acknowledge_prior_exposure=True)["independence"] == "repeat_exposure"


def test_an_interrupted_evaluation_is_exposure_uncertain(lab):
    _register(lab)
    for kind in ("authorized", "started"):  # process died before an outcome event
        append_event(lab["data"], "reg-a-v1", {"type": kind, "attempt_id": "x", "role": "holdout"})
    loaded = load(lab["data"], "reg-a-v1")
    assert partition_state(loaded, "holdout", lab["data"], AFTER)["state"] == "exposure_uncertain"
    _shards(lab["data"], HOLDOUT)
    with pytest.raises(ProspectiveError, match="already exposed; it cannot be re-run"):
        _evaluate(lab, role="holdout", protected_holdout=True)


def test_the_holdout_needs_explicit_invocation_and_runs_at_most_once(lab):
    _register(lab)
    _shards(lab["data"], HOLDOUT)
    with pytest.raises(ProspectiveError, match="--protected-holdout"):
        _evaluate(lab, role="holdout")
    first = _evaluate(lab, role="holdout", protected_holdout=True)
    assert first["independence"] == "independent_prospective"
    with pytest.raises(ProspectiveError, match="already exposed"):
        _evaluate(lab, role="holdout", protected_holdout=True, acknowledge_prior_exposure=True)


def test_renaming_or_revising_a_claim_does_not_make_exposed_data_fresh(lab):
    _register(lab)
    _shards(lab["data"], VALIDATION)
    _evaluate(lab)
    # The same claim, re-registered under a new id, over an overlapping
    # period it could not have registered before the data existed — so
    # build it as if registered earlier (a forged timeline still can't
    # make the period fresh).
    other = _registration(lab, rid="reg-b-v1", partitions=[VALIDATION], supersedes="reg-a-v1")
    target = lab["data"] / "evaluations" / "reg-b-v1"
    (target / "registration").mkdir(parents=True)
    (target / "events").mkdir()
    (target / "registration" / "registration.json").write_text(
        json.dumps(
            {
                "registration": other.model_dump(mode="json"),
                "registration_hash": prospective.registration_hash(other),
            }
        )
    )
    completed = _evaluate(lab, rid="reg-b-v1")
    assert completed["independence"] == "claim_previously_exposed"
    assert completed["independence_details"]["overlapping_exposures_same_claim_or_lineage"] == [
        "reg-a-v1/validation"
    ]


def test_shards_generated_before_registration_make_independence_undetermined(lab):
    _register(lab)
    _shards(lab["data"], VALIDATION, generated_at=REGISTERED - timedelta(days=1))
    assert _evaluate(lab)["independence"] == "undetermined"


# -- the knowledge ledger -----------------------------------------------------------------------


def _check(lab):
    return lambda record: verify_prospective_run(lab["data"], record)


def _records(lab):
    return load_runs(lab["data"] / "runs").records


def test_independent_prospective_validation_supports_the_registered_prediction(lab):
    _register(lab)
    before = derive_ledger(lab["campaign"], lab["specs"], _records(lab))
    assert before["hypotheses"][0]["knowledge_status"] == "insufficient_evidence"
    _shards(lab["data"], VALIDATION)
    _evaluate(lab)
    ledger = derive_ledger(lab["campaign"], lab["specs"], _records(lab))
    [h1] = ledger["hypotheses"]
    assert (h1["knowledge_status"], h1["validation_status"]) == (
        "independently_supported",
        "prospectively_validated",
    )
    classes = {e["evidence_class"] for e in h1["evidence"]}
    assert {"development", "prospective_validation"} <= classes
    revision = {**ledger, "revision": 1, "previous_revision_hash": None, "derived_at": "x"}
    from app.research.knowledge import content_hash

    revision["content_hash"] = content_hash(revision)
    assert (
        verify_revision(revision, lab["campaign"], lab["specs"], _records(lab), None, _check(lab))
        == []
    )
    # Without the exposure-log check a prospective run is not trusted.
    assert any(
        "cannot be checked" in p
        for p in verify_revision(revision, lab["campaign"], lab["specs"], _records(lab), None)
    )


def test_reused_prospective_evidence_never_upgrades_knowledge(lab):
    _register(lab)
    _shards(lab["data"], VALIDATION)
    for kind in ("authorized", "started"):  # an earlier, uncertain exposure
        append_event(
            lab["data"], "reg-a-v1", {"type": kind, "attempt_id": "old", "role": "validation"}
        )
    _evaluate(lab, acknowledge_prior_exposure=True)
    [h1] = derive_ledger(lab["campaign"], lab["specs"], _records(lab))["hypotheses"]
    assert h1["knowledge_status"] == "insufficient_evidence"
    assert h1["validation_status"] == "prospective_evidence_not_independent"
    assert any("not independent (repeat_exposure)" in note for note in h1["limitations"])


def test_a_forged_independence_claim_is_rejected(lab):
    _register(lab)
    _shards(lab["data"], VALIDATION)
    for kind in ("authorized", "started"):
        append_event(
            lab["data"], "reg-a-v1", {"type": kind, "attempt_id": "old", "role": "validation"}
        )
    _evaluate(lab, acknowledge_prior_exposure=True)
    [record] = [r for r in _records(lab) if r.get("kind") == "prospective_evaluation"]
    path = lab["data"] / "runs" / record["experiment_id"] / record["run_id"] / "result.json"
    forged = {**record, "independence": "independent_prospective"}
    path.write_text(json.dumps(forged))
    assert any("independence differs" in p for p in verify_prospective_run(lab["data"], forged))
    ledger = derive_ledger(lab["campaign"], lab["specs"], _records(lab))
    revision = {**ledger, "revision": 1, "previous_revision_hash": None, "derived_at": "x"}
    from app.research.knowledge import content_hash

    revision["content_hash"] = content_hash(revision)
    problems = verify_revision(
        revision, lab["campaign"], lab["specs"], _records(lab), None, _check(lab)
    )
    assert any("independence differs" in p for p in problems)
