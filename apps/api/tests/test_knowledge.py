import copy
import json

import pytest
from test_campaign import _campaign, _rows

from app.research.campaign import campaign_hash, load_campaign
from app.research.knowledge import (
    LedgerError,
    classify,
    collect_evidence,
    content_hash,
    derive_ledger,
    load_revisions,
    missing_evidence,
    prediction_sign,
    verify_revision,
    write_revision,
)
from app.research.runs import load_runs, write_run
from app.scripts import run_experiment
from app.scripts.run_campaign import run_comparison


def _setup(tmp_path, days=2, bonus=0.01, prediction="Positive incremental spread."):
    """A real campaign (H1: candidate vs price-only) with real development
    runs of both experiments and the comparison."""
    path, experiments, data = _campaign(tmp_path, _rows(days, oi_down_bonus=bonus))
    registered = json.loads(path.read_text())
    registered["hypotheses"][0]["prediction"] = prediction
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
    return campaign, specs, data


def _records(data):
    return load_runs(data / "runs").records


# -- the rules -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "sign"),
    [
        ("Positive incremental spread versus the baseline.", "positive"),
        ("Negative spread expected.", "negative"),
        ("No direction assumed: either sign is informative.", "none"),
        ("It will probably go up.", "unspecified"),
    ],
)
def test_only_registered_prediction_forms_carry_a_sign(text, sign):
    assert prediction_sign(text) == sign


def _e(verdict, independent=None):
    return {"verdict": verdict, "independent": independent}


@pytest.mark.parametrize(
    ("sign", "dev", "val", "expected"),
    [
        ("positive", None, None, ("untested", "not_validated")),
        ("positive", _e("insufficient_data"), None, ("insufficient_evidence", "not_validated")),
        ("positive", _e("inconclusive"), None, ("inconclusive", "not_validated")),
        ("positive", _e("positive_evidence"), None, ("preliminary_support", "not_validated")),
        ("positive", _e("negative_evidence"), None, ("preliminary_contradiction", "not_validated")),
        # An unsigned prediction can only ever show a difference.
        ("none", _e("positive_evidence"), None, ("preliminary_difference", "not_validated")),
        ("unspecified", _e("negative_evidence"), None, ("preliminary_difference", "not_validated")),
        (
            "positive",
            _e("positive_evidence"),
            _e("positive_evidence", True),
            ("independently_supported", "independently_validated"),
        ),
        (
            "positive",
            _e("negative_evidence"),
            _e("negative_evidence", True),
            ("independently_contradicted", "independently_validated"),
        ),
        (
            "none",
            _e("positive_evidence"),
            _e("positive_evidence", True),
            ("independently_observed_difference", "independently_validated"),
        ),
        (
            "positive",
            _e("positive_evidence"),
            _e("negative_evidence", True),
            ("conflicting_evidence", "independently_validated"),
        ),
        # Reused validation days never upgrade a status.
        (
            "positive",
            _e("positive_evidence"),
            _e("positive_evidence", False),
            ("preliminary_support", "validation_not_independent"),
        ),
        # A failed-to-confirm validation leaves only the preliminary status.
        (
            "positive",
            _e("positive_evidence"),
            _e("insufficient_data", True),
            ("preliminary_support", "independently_validated"),
        ),
    ],
)
def test_knowledge_status_is_the_weakest_state_the_evidence_justifies(sign, dev, val, expected):
    assert classify(sign, dev, val) == expected


# -- deriving from real runs -------------------------------------------------------------------


def test_insufficient_runs_derive_insufficient_evidence_with_reasons(tmp_path):
    campaign, specs, data = _setup(tmp_path)
    ledger = derive_ledger(campaign, specs, _records(data))
    [h1] = ledger["hypotheses"]
    assert (h1["knowledge_status"], h1["validation_status"]) == (
        "insufficient_evidence",
        "not_validated",
    )
    assert h1["prediction_sign"] == "positive" and h1["direction"] == "long"
    roles = {e["experiment_id"]: e["role"] for e in h1["evidence"]}
    assert roles == {"h1-vs-price-only-v1": "primary", "h1-long-v1": "context"}
    assert any("qualifying days" in note for note in h1["limitations"])
    assert "No validation run: nothing is independently confirmed." in h1["limitations"]
    assert ledger["campaign_hash"] == campaign_hash(campaign, specs)


def test_enough_days_with_a_real_effect_is_only_preliminary_without_validation(tmp_path):
    campaign, specs, data = _setup(tmp_path, days=14)
    [h1] = derive_ledger(campaign, specs, _records(data))["hypotheses"]
    assert h1["knowledge_status"] == "preliminary_support"
    assert h1["validation_status"] == "not_validated"


def test_a_real_effect_for_an_unsigned_prediction_is_only_a_difference(tmp_path):
    campaign, specs, data = _setup(tmp_path, days=14, prediction="It will matter somehow.")
    [h1] = derive_ledger(campaign, specs, _records(data))["hypotheses"]
    assert (h1["prediction_sign"], h1["knowledge_status"]) == (
        "unspecified",
        "preliminary_difference",
    )


def test_repeated_runs_are_repeats_not_independent_evidence(tmp_path):
    campaign, specs, data = _setup(tmp_path)
    run_comparison(
        campaign,
        campaign_hash(campaign, specs),
        campaign.comparisons[0],
        specs,
        "development",
        data,
    )
    evidence = collect_evidence(campaign, "H1", _records(data))
    primary = [e for e in evidence if e["role"] == "primary"]
    assert [e["current"] for e in primary] == [True, False]
    assert (
        derive_ledger(campaign, specs, _records(data))["hypotheses"][0]["knowledge_status"]
        == "insufficient_evidence"
    )


def test_disagreeing_repeats_refuse_derivation(tmp_path):
    campaign, specs, data = _setup(tmp_path)
    record = next(r for r in _records(data) if r["experiment_id"] == "h1-vs-price-only-v1")
    forged = copy.deepcopy(record)
    forged["run_id"] = "20000101T000000000000Z-000000"
    forged["executed_at"] = "2000-01-01T00:00:00+00:00"
    forged["report"]["verdict"] = "positive_evidence"
    write_run(data / "runs", forged, {})
    with pytest.raises(LedgerError, match="disagree"):
        derive_ledger(campaign, specs, _records(data))


# -- revisions and verification ----------------------------------------------------------------


def test_revisions_are_appended_only_when_knowledge_changes(tmp_path):
    campaign, specs, data = _setup(tmp_path)
    root = tmp_path / "knowledge"
    path, first = write_revision(root, derive_ledger(campaign, specs, _records(data)))
    assert path is not None and first["revision"] == 1 and first["previous_revision_hash"] is None
    again, latest = write_revision(root, derive_ledger(campaign, specs, _records(data)))
    assert again is None and latest == first  # nothing new: no revision
    run_comparison(
        campaign,
        campaign_hash(campaign, specs),
        campaign.comparisons[0],
        specs,
        "development",
        data,
    )
    _, second = write_revision(root, derive_ledger(campaign, specs, _records(data)))
    assert second["revision"] == 2 and second["previous_revision_hash"] == first["content_hash"]
    assert json.loads(path.read_text()) == first  # revision 1 untouched
    revisions = load_revisions(root, campaign.campaign_id)
    assert verify_revision(revisions[0], campaign, specs, _records(data), None) == []
    assert verify_revision(revisions[1], campaign, specs, _records(data), revisions[0]) == []


def _revision(tmp_path):
    campaign, specs, data = _setup(tmp_path)
    _, revision = write_revision(tmp_path / "k", derive_ledger(campaign, specs, _records(data)))
    return campaign, specs, data, revision


def _rehash(revision):
    revision["content_hash"] = content_hash(revision)
    return revision


def test_a_status_the_evidence_does_not_support_is_rejected(tmp_path):
    campaign, specs, data, revision = _revision(tmp_path)
    forged = copy.deepcopy(revision)
    forged["hypotheses"][0]["knowledge_status"] = "independently_supported"
    forged["hypotheses"][0]["validation_status"] = "independently_validated"
    problems = verify_revision(_rehash(forged), campaign, specs, _records(data), None)
    assert any("knowledge_status is not what its cited evidence supports" in p for p in problems)


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        (
            lambda r: r["hypotheses"][0]["evidence"][0].update(
                run_id="20991231T000000000000Z-ffffff"
            ),
            "does not exist",
        ),
        (
            lambda r: r["hypotheses"][0]["evidence"][0].update(verdict="positive_evidence"),
            "verdict differs",
        ),
        (
            lambda r: r["hypotheses"][0]["evidence"][0].update(segment="validation"),
            "segment differs",
        ),
        (
            lambda r: r["hypotheses"][0]["evidence"][0].update(experiment_id="base-long-v1"),
            "not evidence registered",
        ),
        (
            lambda r: r["hypotheses"][0].update(prediction="Positive, and large."),
            "differs from the registration",
        ),
        (lambda r: r.update(campaign_hash="0" * 64), "campaign_hash does not match"),
        (lambda r: r.update(scope="model_level"), "only feature_level"),
    ],
)
def test_references_that_disagree_with_immutable_records_are_rejected(tmp_path, tamper, message):
    campaign, specs, data, revision = _revision(tmp_path)
    forged = copy.deepcopy(revision)
    tamper(forged)
    problems = verify_revision(_rehash(forged), campaign, specs, _records(data), None)
    assert any(message in p for p in problems), problems


def test_an_edited_revision_or_broken_chain_is_detected(tmp_path):
    campaign, specs, data, revision = _revision(tmp_path)
    edited = copy.deepcopy(revision)
    edited["hypotheses"][0]["limitations"] = []
    assert any(
        "content_hash" in p for p in verify_revision(edited, campaign, specs, _records(data), None)
    )
    assert any(
        "revision chain" in p
        for p in verify_revision(revision, campaign, specs, _records(data), revision)
    )


def test_a_changed_campaign_invalidates_its_ledger(tmp_path):
    campaign, specs, data, revision = _revision(tmp_path)
    spec_path = tmp_path / "experiments" / "h1-long-v1.json"
    spec = json.loads(spec_path.read_text())
    spec["hypothesis"]["statement"] = "reworded"
    spec_path.write_text(json.dumps(spec))
    campaign, specs = load_campaign(
        tmp_path / "campaigns" / "test-campaign-v1.json", tmp_path / "experiments"
    )
    problems = verify_revision(revision, campaign, specs, _records(data), None)
    assert any("campaign_hash does not match" in p for p in problems)


def test_newer_runs_make_a_ledger_stale_not_wrong(tmp_path):
    campaign, specs, data, revision = _revision(tmp_path)
    run_comparison(
        campaign,
        campaign_hash(campaign, specs),
        campaign.comparisons[0],
        specs,
        "development",
        data,
    )
    assert verify_revision(revision, campaign, specs, _records(data), None) == []
    assert len(missing_evidence(revision, campaign, _records(data))) == 1
