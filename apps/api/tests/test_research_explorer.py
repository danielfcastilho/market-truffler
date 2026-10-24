import hashlib
import json
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.services.research_artifacts import InvalidIdentifier, ResearchArtifacts

CAMPAIGN = "price-oi-v1"
BASE, CAND, CMP = "base-long-v1", "cand-long-v1", "cand-vs-base-v1"
OLD_RUN = "20261024T100000000000Z-aaaaaa"
NEW_RUN = "20261024T120000000000Z-bbbbbb"


def _spec(experiment_id: str, conditions: int) -> dict:
    return {
        "spec_version": 1,
        "experiment_id": experiment_id,
        "heuristic": {"id": "h", "text": "t"},
        "hypothesis": {
            "statement": "s",
            "direction": "long",
            "conditions": [
                {"feature": "return_1h", "transform": "cs_percentile", "op": "<=", "value": 0.2},
                {"feature": "oi_change_1h", "transform": "raw", "op": "<", "value": 0.0},
            ][:conditions],
        },
        "datasets": [{"name": "shard_x", "data_sha256": "0" * 64}],
        "label": "forward_relative_return_1h",
    }


def _record(experiment_id: str, run_id: str, verdict: str, **extra) -> dict:
    return {
        "run_id": run_id,
        "experiment_id": experiment_id,
        "executed_at": f"2026-10-24T{run_id[9:11]}:00:00+00:00",
        "segment": "development",
        "status": "completed",
        "spec_hash": "a" * 64,
        "report": {"verdict": verdict, "verdict_reasons": ["1 qualifying days in segment (< 10)"]},
        **extra,
    }


def _write_run(data: Path, record: dict) -> None:
    run_dir = data / "runs" / record["experiment_id"] / record["run_id"]
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(json.dumps(record))
    (run_dir / "spec.json").write_text("{}")


@pytest.fixture
def artifacts(tmp_path, monkeypatch) -> Path:
    campaigns, experiments, data = tmp_path / "c", tmp_path / "e", tmp_path / "d"
    for d in (campaigns, experiments, data):
        d.mkdir()
    (campaigns / f"{CAMPAIGN}.json").write_text(
        json.dumps(
            {
                "campaign_version": 1,
                "campaign_id": CAMPAIGN,
                "created": "2026-10-24",
                "question": "Does OI add information?",
                "motivation": "m",
                "hypotheses": [
                    {"id": "H1", "statement": "s", "prediction": "p", "experiment_id": CAND}
                ],
                "baselines": [
                    {"id": "eligible-universe", "description": "built in"},
                    {"id": "price-only", "description": "d", "experiment_id": BASE},
                ],
                "comparisons": [
                    {"comparison_id": CMP, "hypothesis": "H1", "candidate": CAND, "reference": BASE}
                ],
                "parameters_rationale": "r",
                "evaluation_plan": "e",
            }
        )
    )
    (campaigns / f"{CAMPAIGN}.report.md").write_text(
        "# Report\n\nintro\n\n## Conclusion\n\nInsufficient data.\n\n## Knowledge status\n\n- H1\n"
    )
    (experiments / f"{BASE}.json").write_text(json.dumps(_spec(BASE, 1)))
    (experiments / f"{CAND}.json").write_text(json.dumps(_spec(CAND, 2)))
    _write_run(data, _record(BASE, OLD_RUN, "inconclusive"))
    _write_run(data, _record(BASE, NEW_RUN, "insufficient_data"))  # supersedes the older run
    _write_run(data, _record(CAND, NEW_RUN, "insufficient_data"))
    _write_run(
        data,
        _record(
            CMP,
            NEW_RUN,
            "insufficient_data",
            kind="incremental_comparison",
            campaign_id=CAMPAIGN,
            campaign_hash="c" * 64,
            candidate={"experiment_id": CAND},
            reference={"experiment_id": BASE},
        ),
    )
    (data / "runs" / BASE / ".20261024T130000000000Z-cccccc.tmp-dead").mkdir()  # incomplete
    knowledge = tmp_path / "k" / CAMPAIGN
    knowledge.mkdir(parents=True)
    (knowledge / "0001.json").write_text(json.dumps(_revision()))
    settings = get_settings()
    monkeypatch.setattr(settings, "research_campaigns_dir", str(campaigns))
    monkeypatch.setattr(settings, "research_experiments_dir", str(experiments))
    monkeypatch.setattr(settings, "research_data_dir", str(data))
    monkeypatch.setattr(settings, "research_knowledge_dir", str(tmp_path / "k"))
    return tmp_path


def _revision(**overrides) -> dict:
    """A ledger revision as `app.scripts.knowledge_ledger` writes it."""
    evidence = [
        {
            "experiment_id": CMP,
            "run_id": NEW_RUN,
            "role": "primary",
            "segment": "development",
            "verdict": "insufficient_data",
            "current": True,
        },
        {
            "experiment_id": CAND,
            "run_id": NEW_RUN,
            "role": "context",
            "segment": "development",
            "verdict": "insufficient_data",
            "current": True,
        },
    ]
    return {
        "ledger_schema_version": 1,
        "knowledge_rules_version": 1,
        "campaign_id": CAMPAIGN,
        "campaign_hash": "c" * 64,
        "revision": 1,
        "previous_revision_hash": None,
        "derived_at": "2026-10-24T13:00:00+00:00",
        "content_hash": "d" * 64,
        "scope": "feature_level",
        "hypotheses": [
            {
                "hypothesis_id": "H1",
                "experiment_id": CAND,
                "primary_evidence_ids": [CMP],
                "direction": "long",
                "knowledge_status": "insufficient_evidence",
                "validation_status": "not_validated",
                "evidence": evidence,
                "limitations": ["development: 1 qualifying days in segment (< 10)"],
            }
        ],
        **overrides,
    }


async def _login(client, test_user):
    response = await client.post(
        "/api/auth/login", json={"email": test_user.email, "password": "correct-horse-battery"}
    )
    assert response.status_code == 200


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(str(path.relative_to(root)).encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


@pytest.mark.parametrize(
    "path",
    [
        "/api/research/overview",
        f"/api/research/campaigns/{CAMPAIGN}",
        f"/api/research/experiments/{BASE}",
        "/api/research/history",
    ],
)
async def test_every_explorer_endpoint_requires_authentication(client, path):
    assert (await client.get(path)).status_code == 401


async def test_overview_counts_artifacts_and_latest_verdicts_only(client, test_user, artifacts):
    await _login(client, test_user)
    body = (await client.get("/api/research/overview")).json()
    counts = body["counts"]
    assert (counts["campaigns"], counts["hypotheses"]) == (1, 1)
    assert (counts["experiments_registered"], counts["experiments_executed"]) == (2, 2)
    assert (counts["comparisons_registered"], counts["comparisons_executed"]) == (1, 1)
    assert counts["runs"] == 4  # the hidden incomplete directory is not a run
    # One verdict per experiment/comparison: the older BASE run doesn't count.
    assert counts["latest_verdicts"]["development"] == {
        "positive_evidence": 0,
        "negative_evidence": 0,
        "inconclusive": 0,
        "insufficient_data": 3,
    }
    [campaign] = body["campaigns"]
    assert campaign["latest_activity"].startswith("2026-10-24T12")
    assert (campaign["ledger_revision"], campaign["ledger_problems"]) == (1, 0)
    [h1] = campaign["hypotheses"]
    assert (h1["hypothesis_id"], h1["direction"], h1["knowledge_status"]) == (
        "H1",
        "long",
        "insufficient_evidence",
    )
    assert [c["feature"] for c in h1["conditions"]] == ["return_1h", "oi_change_1h"]
    assert body["knowledge"] == {
        "by_status": {"insufficient_evidence": 1},
        "hypotheses_without_ledger": 0,
    }
    kinds = [e["type"] for e in body["activity"]]
    # All four runs belong to the campaign and share a UTC day: one evaluation.
    assert kinds == ["knowledge_revision", "evaluation", "campaign_registered"]
    revision = body["activity"][0]
    assert revision["changes"] == [
        {"hypothesis_id": "H1", "from": None, "to": "insufficient_evidence"}
    ]
    latest_evaluation = body["activity"][1]
    assert (latest_evaluation["runs"], latest_evaluation["repeat_runs"]) == (4, 1)
    assert body["issues"] == []


async def test_empty_research_directories_give_an_honest_empty_overview(
    client, test_user, tmp_path, monkeypatch
):
    settings = get_settings()
    for name in (
        "research_campaigns_dir",
        "research_experiments_dir",
        "research_data_dir",
        "research_knowledge_dir",
    ):
        monkeypatch.setattr(settings, name, str(tmp_path / "missing"))
    await _login(client, test_user)
    body = (await client.get("/api/research/overview")).json()
    assert body["campaigns"] == [] and body["activity"] == []
    assert body["counts"]["runs"] == 0 and body["counts"]["campaigns"] == 0


async def test_campaign_detail_relates_specs_runs_comparisons_and_report(
    client, test_user, artifacts
):
    await _login(client, test_user)
    body = (await client.get(f"/api/research/campaigns/{CAMPAIGN}")).json()
    assert body["campaign"]["question"] == "Does OI add information?"
    assert set(body["specs"]) == {BASE, CAND}
    assert [r["run_id"] for r in body["experiment_runs"][BASE]] == [NEW_RUN, OLD_RUN]
    assert body["comparison_runs"][CMP][0]["report"]["verdict"] == "insufficient_data"
    assert body["campaign_hashes"] == ["c" * 64]
    sections = {s["title"]: s["body"] for s in body["report"]}
    assert sections["Conclusion"] == "Insufficient data."


async def test_experiment_detail_lists_its_runs_campaign_roles_and_comparisons(
    client, test_user, artifacts
):
    await _login(client, test_user)
    body = (await client.get(f"/api/research/experiments/{BASE}")).json()
    assert body["spec"]["experiment_id"] == BASE
    assert [r["run_id"] for r in body["runs"]] == [NEW_RUN, OLD_RUN]
    [campaign] = body["campaigns"]
    assert campaign["roles"] == ["baseline price-only", f"reference in {CMP}"]
    [comparison] = body["comparisons"]
    assert comparison["role"] == "reference" and len(comparison["runs"]) == 1


@pytest.mark.parametrize(
    "path",
    [
        "/api/research/campaigns/unknown-v1",
        "/api/research/campaigns/Bad_Id",
        "/api/research/campaigns/..",
        "/api/research/campaigns/%2E%2E%2Fsecret",
        "/api/research/experiments/unknown-v1",
        "/api/research/experiments/..%2F..%2Fetc%2Fpasswd",
    ],
)
async def test_unknown_or_invalid_identifiers_are_not_found(client, test_user, artifacts, path):
    await _login(client, test_user)
    response = await client.get(path)
    assert response.status_code == 404
    assert str(artifacts) not in response.text  # no filesystem paths leak


async def test_malformed_artifacts_are_reported_not_silently_dropped(client, test_user, artifacts):
    (artifacts / "c" / "broken-v1.json").write_text("{not json")
    (artifacts / "e" / "renamed-v1.json").write_text(json.dumps(_spec("other-v1", 1)))
    bad = artifacts / "d" / "runs" / BASE / "20261024T140000000000Z-dddddd"
    bad.mkdir()
    (bad / "result.json").write_text("[]")
    moved = artifacts / "d" / "runs" / CAND / "20261024T150000000000Z-eeeeee"
    moved.mkdir()
    (moved / "result.json").write_text(json.dumps(_record(BASE, NEW_RUN, "inconclusive")))
    await _login(client, test_user)
    body = (await client.get("/api/research/overview")).json()
    problems = {i["artifact"]: i["problem"] for i in body["issues"]}
    assert problems["broken-v1.json"].startswith("unreadable")
    assert problems[f"runs/{BASE}/20261024T140000000000Z-dddddd/result.json"] == "not a JSON object"
    assert problems[f"runs/{CAND}/20261024T150000000000Z-eeeeee"] == (
        "record ids differ from its directory"
    )
    assert body["counts"]["runs"] == 4  # none of the bad records counted
    response = await client.get("/api/research/experiments/renamed-v1")
    assert response.status_code == 404
    assert all(not i["artifact"].startswith("/") for i in body["issues"])


async def test_reading_never_modifies_research_artifacts(client, test_user, artifacts):
    before = _tree_hash(artifacts)
    await _login(client, test_user)
    for path in (
        "/api/research/overview",
        f"/api/research/campaigns/{CAMPAIGN}",
        f"/api/research/experiments/{BASE}",
        f"/api/research/experiments/{CAND}",
    ):
        assert (await client.get(path)).status_code == 200
    assert _tree_hash(artifacts) == before


def test_identifiers_are_confined_to_their_directories(tmp_path):
    store = ResearchArtifacts(tmp_path / "c", tmp_path / "e", tmp_path / "d")
    for bad in ("../secret", "a/b", "", "UPPER", "x" * 81):
        with pytest.raises(InvalidIdentifier):
            store.spec(bad)
        with pytest.raises(InvalidIdentifier):
            store.campaign(bad)
    with pytest.raises(InvalidIdentifier):
        store.run("ok-v1", "../../etc")


def test_the_explorer_has_no_write_endpoints():
    from app.main import app

    methods = {
        method
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/research")
        for method in getattr(route, "methods", set())
    }
    assert methods <= {"GET", "HEAD"}


async def test_history_lists_every_run_and_artifact_backed_events(client, test_user, artifacts):
    await _login(client, test_user)
    body = (await client.get("/api/research/history")).json()
    assert len(body["runs"]) == 4
    comparison = next(r for r in body["runs"] if r["kind"] == "incremental_comparison")
    assert comparison["link_experiment_id"] == CAND and comparison["direction"] == "long"
    assert comparison["campaign_id"] == CAMPAIGN
    assert {e["type"] for e in body["events"]} == {
        "campaign_registered",
        "evaluation",
        "knowledge_revision",
    }


async def test_campaign_detail_serves_ledger_revisions_and_their_check(
    client, test_user, artifacts
):
    await _login(client, test_user)
    body = (await client.get(f"/api/research/campaigns/{CAMPAIGN}")).json()
    [revision] = body["knowledge_revisions"]
    assert revision["hypotheses"][0]["knowledge_status"] == "insufficient_evidence"
    assert body["knowledge_check"] == {"problems": [], "uncited_runs": []}
    experiment = (await client.get(f"/api/research/experiments/{CAND}")).json()
    assert experiment["knowledge"] == [
        {
            "campaign_id": CAMPAIGN,
            "revision": 1,
            "hypothesis_id": "H1",
            "knowledge_status": "insufficient_evidence",
            "validation_status": "not_validated",
        }
    ]


async def test_a_ledger_citing_missing_or_different_runs_is_flagged(client, test_user, artifacts):
    forged = _revision()
    forged["hypotheses"][0]["evidence"][0]["verdict"] = "positive_evidence"
    forged["hypotheses"][0]["evidence"][1]["run_id"] = "20991231T000000000000Z-ffffff"
    (artifacts / "k" / CAMPAIGN / "0001.json").write_text(json.dumps(forged))
    await _login(client, test_user)
    body = (await client.get(f"/api/research/campaigns/{CAMPAIGN}")).json()
    problems = body["knowledge_check"]["problems"]
    assert any("differs from its record" in p for p in problems)
    assert any("does not exist" in p for p in problems)
    assert body["knowledge_check"]["uncited_runs"] == [f"H1: {CAND}/{NEW_RUN}"]
    overview = (await client.get("/api/research/overview")).json()
    assert overview["campaigns"][0]["ledger_problems"] == 2


async def test_a_malformed_or_misplaced_ledger_revision_is_reported(client, test_user, artifacts):
    ledger = artifacts / "k" / CAMPAIGN
    (ledger / "0001.json").write_text("{broken")
    (ledger / "0002.json").write_text(json.dumps(_revision(revision=7)))
    await _login(client, test_user)
    body = (await client.get(f"/api/research/campaigns/{CAMPAIGN}")).json()
    assert body["knowledge_revisions"] == [] and body["knowledge_check"] is None
    problems = {i["artifact"]: i["problem"] for i in body["issues"]}
    assert problems[f"{CAMPAIGN}/0001.json"].startswith("unreadable")
    assert problems[f"{CAMPAIGN}/0002.json"] == "revision ids differ from its file"
    overview = (await client.get("/api/research/overview")).json()
    assert overview["knowledge"]["hypotheses_without_ledger"] == 1


def _write_evaluation(data: Path, *, events=(), validation=("2026-11-01", "2026-11-03")):
    directory = data / "evaluations" / "reg-v1"
    (directory / "registration").mkdir(parents=True)
    (directory / "events").mkdir()
    registration = {
        "protocol_version": 1,
        "registration_id": "reg-v1",
        "registered_at": "2026-10-24T20:00:00+00:00",
        "campaign_id": CAMPAIGN,
        "claims": [{"hypothesis_id": "H1"}],
        "plan": {"embargo_minutes": 240},
        "partitions": [{"role": "validation", "start": validation[0], "end": validation[1]}],
        "timestamp_trust": "local clock",
    }
    (directory / "registration" / "registration.json").write_text(
        json.dumps({"registration": registration, "registration_hash": "r" * 64})
    )
    for seq, event in enumerate(events, start=1):
        (directory / "events" / f"{seq:06d}").mkdir()
        (directory / "events" / f"{seq:06d}" / "event.json").write_text(
            json.dumps({"seq": seq, "role": "validation", "attempt_id": "a", **event})
        )


async def test_a_registered_prospective_evaluation_is_pending_until_its_data_exists(
    client, test_user, artifacts
):
    _write_evaluation(artifacts / "d", validation=("2099-01-01", "2099-01-31"))
    await _login(client, test_user)
    overview = (await client.get("/api/research/overview")).json()
    [evaluation] = overview["campaigns"][0]["evaluations"]
    [partition] = evaluation["partitions"]
    assert (partition["state"], partition["exposed"], partition["days"]) == (
        "awaiting_data",
        False,
        30,
    )
    assert partition["eligible_from"].startswith("2099-02-02")  # no shards: end + 2 days
    assert any(e["type"] == "evaluation_registered" for e in overview["activity"])
    detail = (await client.get(f"/api/research/campaigns/{CAMPAIGN}")).json()
    assert detail["evaluations"][0]["events"] == []


async def test_exposure_is_reported_from_the_event_log(client, test_user, artifacts):
    _write_evaluation(artifacts / "d", events=[{"type": "authorized"}, {"type": "started"}])
    await _login(client, test_user)
    detail = (await client.get(f"/api/research/campaigns/{CAMPAIGN}")).json()
    [partition] = detail["evaluations"][0]["partitions"]
    assert (partition["state"], partition["exposed"], partition["attempts"]) == (
        "exposure_uncertain",
        True,
        1,
    )
    assert [e["type"] for e in detail["evaluations"][0]["events"]] == ["authorized", "started"]


async def test_a_malformed_registration_is_reported(client, test_user, artifacts):
    directory = artifacts / "d" / "evaluations" / "bad-v1" / "registration"
    directory.mkdir(parents=True)
    (directory / "registration.json").write_text(
        json.dumps({"registration": {"registration_id": "x"}})
    )
    await _login(client, test_user)
    overview = (await client.get("/api/research/overview")).json()
    assert {"artifact": "evaluations/bad-v1", "problem": "registration id differs"} in overview[
        "issues"
    ]


def _write_ranking(root: Path, data: Path) -> None:
    (root / "candidates").mkdir(parents=True)
    (root / "studies").mkdir()
    for cid in ("long-price-v1", "long-price-oi-v1"):
        (root / "candidates" / f"{cid}.json").write_text(
            json.dumps({"candidate_id": cid, "name": cid, "direction": "long", "terms": []})
        )
    (root / "candidates" / "renamed-v1.json").write_text(json.dumps({"candidate_id": "other-v1"}))
    (root / "studies" / "long-study-v1.json").write_text(
        json.dumps(
            {
                "study_id": "long-study-v1",
                "candidate": "long-price-oi-v1",
                "reference": "long-price-v1",
            }
        )
    )
    _write_run(
        data,
        _record(
            "long-study-v1",
            "20261024T140000000000Z-eeeeee",
            "insufficient_data",
            kind="ranking_study",
        ),
    )


async def test_ranking_research_is_served_apart_from_feature_research(
    client, test_user, artifacts, monkeypatch
):
    _write_ranking(artifacts / "r", artifacts / "d")
    monkeypatch.setattr(get_settings(), "research_ranking_dir", str(artifacts / "r"))
    assert (await client.get("/api/research/ranking")).status_code == 401
    await _login(client, test_user)
    body = (await client.get("/api/research/ranking")).json()
    [study] = body["studies"]
    assert study["candidate"]["candidate_id"] == "long-price-oi-v1"
    assert study["reference"]["candidate_id"] == "long-price-v1"
    assert [r["run_id"] for r in study["runs"]] == ["20261024T140000000000Z-eeeeee"]
    assert {i["problem"] for i in body["issues"]} == {"candidate_id differs"}
    history = (await client.get("/api/research/history")).json()
    [run] = [r for r in history["runs"] if r["kind"] == "ranking_study"]
    assert run["link_experiment_id"] is None  # not an experiment page
    assert any(e["type"] == "ranking_evaluation" for e in history["events"])
    assert not any(
        e["type"] == "evaluation" and "long-study-v1" in e["experiment_ids"]
        for e in history["events"]
    )


async def test_no_ranking_research_is_an_empty_list(client, test_user, artifacts, monkeypatch):
    monkeypatch.setattr(get_settings(), "research_ranking_dir", str(artifacts / "missing"))
    await _login(client, test_user)
    body = (await client.get("/api/research/ranking")).json()
    assert body == {"candidates": [], "studies": [], "issues": []}
