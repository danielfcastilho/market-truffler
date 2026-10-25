"""Model knowledge (app.research.model_knowledge): stable identities,
idempotent append-only revisions, evidence linkage, and statuses that
cannot claim more than the evidence."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_truffle_evaluation import (
    AFTER,
    PAIRS,
    RANKING,
    VALIDATION,
    _evaluate,
    _register,
    _shards,
)

from app.research import model_knowledge as mk
from app.research.knowledge import LedgerError, content_hash, load_revisions_in

REPO = Path(__file__).resolve().parents[3]
REAL_DATA = REPO / "research/oink_corp/data"


@pytest.fixture
def lab(tmp_path):
    ranking = tmp_path / "ranking"
    shutil.copytree(RANKING, ranking)
    data = tmp_path / "data"
    data.mkdir()
    return {"data": data, "ranking": ranking, "knowledge": tmp_path / "knowledge"}


def _derive(lab):
    return {d["identity"]["id"]: d for d in mk.derive_all(lab["ranking"], lab["data"], PAIRS)}


def _sync(lab, now=None):
    return mk.sync(lab["knowledge"], mk.derive_all(lab["ranking"], lab["data"], PAIRS), now=now)


def _verify_all(lab):
    return {
        f"{i['kind']}/{i['id']}": mk.verify_ledger(lab["knowledge"], i, lab["data"])
        for i in mk.identities(lab["ranking"], PAIRS)
    }


def test_models_and_policies_are_distinct_stable_identities(lab):
    first, second = _derive(lab), _derive(lab)
    assert first == second
    long_model = first["long-price-reversal-oi-v1"]["identity"]
    policy = first["long-reversal-oi-truffle-v1"]["identity"]
    assert long_model["kind"] == "ranking_model" and policy["kind"] == "qualification_policy"
    assert long_model["parameters"]["weights"] == {"return_1h": -1.0, "oi_change_1h": -0.5}
    assert long_model["transforms"] == {
        "return_1h": "cs_percentile",
        "oi_change_1h": "cs_percentile",
    }
    assert policy["applies_to"] == {
        "ranking_model": "long-price-reversal-oi-v1",
        "direction": "long",
    }
    assert policy["spec_hash"] != long_model["spec_hash"]
    assert first["long-price-reversal-oi-v1"]["links"] == {
        "qualification_policies": ["long-reversal-oi-truffle-v1"]
    }


def test_initial_knowledge_is_experimental_and_human_hypotheses_are_not_evidence(lab):
    for item in _derive(lab).values():
        assert item["status"] == "experimental" and item["status_basis"] == []
        assert item["evidence_counts"] == {"human_hypothesis": 1}
        assert "No independent prospective evidence" in " ".join(item["limitations"])


def test_sync_is_idempotent_and_append_only(lab):
    written = _sync(lab, now=datetime(2026, 10, 25, tzinfo=UTC))
    assert all(path is not None for _, path in written) and len(written) == 4
    assert all(path is None for _, path in _sync(lab))  # nothing changed: nothing written
    _register(lab)  # new evidence: one more revision each, the first untouched
    first = json.loads(next(lab["knowledge"].rglob("0001.json")).read_text())
    _sync(lab)
    for directory in {p.parent for p in lab["knowledge"].rglob("0001.json")}:
        revisions = load_revisions_in(directory)
        assert len(revisions) == 2
        assert revisions[1]["previous_revision_hash"] == revisions[0]["content_hash"]
    assert json.loads(next(lab["knowledge"].rglob("0001.json")).read_text()) == first
    assert all(problems == [] for ledger in _verify_all(lab).values() for _, problems in ledger)


def test_registration_links_both_identities_without_validating_them(lab):
    _register(lab)
    derived = _derive(lab)
    for item in derived.values():
        assert item["status"] == "registered_for_prospective_evaluation"
        assert item["status_basis"] == ["truffle-test-v1"]
        assert item["evidence_counts"]["prospective_registration"] >= 1


def test_prospective_decisions_move_the_policy_only(lab):
    _register(lab)
    _shards(lab["data"], VALIDATION)
    _evaluate(lab)
    derived = _derive(lab)
    policy = derived["long-reversal-oi-truffle-v1"]
    assert policy["status"] == "supported_by_prospective_evidence"
    assert policy["evidence_counts"]["prospective_validation"] == 1
    model = derived["long-price-reversal-oi-v1"]
    assert model["status"] == "registered_for_prospective_evaluation"
    assert any(e.get("via_qualification_policy") for e in model["evidence"])
    _sync(lab)
    assert all(problems == [] for ledger in _verify_all(lab).values() for _, problems in ledger)


def test_an_unsupported_status_is_rejected_on_verification(lab):
    _sync(lab)
    path = next(p for p in lab["knowledge"].rglob("0001.json") if "qualification" in str(p))
    revision = json.loads(path.read_text())
    revision["status"] = "supported_by_prospective_evidence"
    revision["content_hash"] = content_hash(revision)  # even with a consistent hash
    path.write_text(json.dumps(revision))
    problems = [p for ledger in _verify_all(lab).values() for _, ps in ledger for p in ps]
    assert any("is not what its cited evidence supports" in p for p in problems)


def test_contradictory_evidence_is_kept_and_reported_as_conflict():
    evidence = [
        {
            "class": "prospective_validation",
            "ref": "a/1",
            "decision": "consistent_with_improvement",
        },
        {"class": "protected_holdout", "ref": "a/2", "decision": "inconsistent_with_improvement"},
    ]
    assert mk.derive_status("qualification_policy", evidence) == (
        "conflicting_prospective_evidence",
        ["a/1", "a/2"],
    )


def test_exploratory_or_reused_evidence_never_establishes_a_status():
    evidence = [
        {"class": "exploratory_development", "ref": "x/1", "verdict": "positive_evidence"},
        {"class": "prospective_reused", "ref": "x/2", "decision": "consistent_with_improvement"},
    ]
    for kind in ("ranking_model", "qualification_policy"):
        assert mk.derive_status(kind, evidence) == ("experimental", [])


def test_a_changed_spec_under_the_same_id_is_refused_and_the_old_ledger_kept(lab):
    _sync(lab)
    path = lab["ranking"] / "candidates" / "long-price-reversal-oi-v1.json"
    raw = json.loads(path.read_text())
    raw["terms"][1]["weight"] = -0.75
    path.write_text(json.dumps(raw))
    with pytest.raises(LedgerError, match="needs a new id"):
        _sync(lab)
    model_dir = lab["knowledge"] / "models" / "ranking" / "long-price-reversal-oi-v1"
    assert len(load_revisions_in(model_dir)) == 1


def test_the_real_artifacts_yield_only_experimental_knowledge_with_v1_limitations():
    """The current repository: exploratory ranking studies are linked and
    flagged as temporal contract v1; nothing is validated."""
    derived = {d["identity"]["id"]: d for d in mk.derive_all(RANKING, REAL_DATA, PAIRS)}
    for item in derived.values():
        assert item["status"] in ("experimental", "registered_for_prospective_evaluation")
    model = derived["long-price-reversal-oi-v1"]
    assert model["evidence_counts"]["exploratory_development"] >= 1
    assert any("temporal contract v1" in note for note in model["limitations"])


def test_campaign_ledger_revisions_are_untouched_by_the_shared_writer():
    from app.research.knowledge import load_revisions

    revisions = load_revisions(REPO / "research/oink_corp/knowledge", "oi-incremental-price-1h-v1")
    assert [r["revision"] for r in revisions] == [1, 2]
    assert all(r["content_hash"] == content_hash(r) for r in revisions)


def test_the_status_lifecycle_states_awaiting_until_eligibility(lab):
    from app.research import truffle_evaluation as te

    _register(lab)
    loaded = te.load_truffle(lab["data"], "truffle-test-v1")
    before = te.truffle_status(loaded, lab["data"], datetime(2026, 9, 21, tzinfo=UTC))
    assert [p["lifecycle"] for p in before["partitions"]] == ["registered_awaiting_data"] * 2
    _shards(lab["data"], VALIDATION)
    after = te.truffle_status(loaded, lab["data"], AFTER)
    assert after["partitions"][0]["lifecycle"] == "eligible_for_evaluation"


async def test_the_explorer_shows_models_statuses_and_the_truffle_lifecycle(
    client, test_user, lab, monkeypatch
):
    from app.core.config import get_settings

    _register(lab)
    _sync(lab)
    settings = get_settings()
    monkeypatch.setattr(settings, "research_data_dir", str(lab["data"]))
    monkeypatch.setattr(settings, "research_knowledge_dir", str(lab["knowledge"]))
    monkeypatch.setattr(settings, "research_ranking_dir", str(lab["ranking"]))
    assert (await client.get("/api/research/models")).status_code == 401
    await client.post(
        "/api/auth/login", json={"email": test_user.email, "password": "correct-horse-battery"}
    )
    body = (await client.get("/api/research/models")).json()
    by_id = {m["id"]: m for m in body["models"]}
    assert set(by_id) == {pair for pairs in PAIRS for pair in pairs}
    assert {m["status"] for m in body["models"]} == {"registered_for_prospective_evaluation"}
    assert all(m["chain_problems"] == [] for m in body["models"])
    [registration] = body["truffle_registrations"]
    assert registration["registration_id"] == "truffle-test-v1"
    assert [p["lifecycle"] for p in registration["partitions"]] == ["eligible_for_evaluation"] * 2
    # Existing endpoints still answer.
    assert (await client.get("/api/research/overview")).status_code == 200
