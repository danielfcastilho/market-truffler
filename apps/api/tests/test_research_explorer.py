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
    settings = get_settings()
    monkeypatch.setattr(settings, "research_campaigns_dir", str(campaigns))
    monkeypatch.setattr(settings, "research_experiments_dir", str(experiments))
    monkeypatch.setattr(settings, "research_data_dir", str(data))
    return tmp_path


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
    assert campaign["development_verdicts"]["insufficient_data"] == 1
    assert campaign["comparisons_validated"] == 0
    assert campaign["latest_activity"].startswith("2026-10-24T12")
    comparison = next(r for r in body["recent_runs"] if r["kind"] == "incremental_comparison")
    assert comparison["link_experiment_id"] == CAND and comparison["direction"] == "long"
    assert comparison["campaign_id"] == CAMPAIGN
    assert body["issues"] == []


async def test_empty_research_directories_give_an_honest_empty_overview(
    client, test_user, tmp_path, monkeypatch
):
    settings = get_settings()
    for name in ("research_campaigns_dir", "research_experiments_dir", "research_data_dir"):
        monkeypatch.setattr(settings, name, str(tmp_path / "missing"))
    await _login(client, test_user)
    body = (await client.get("/api/research/overview")).json()
    assert body["campaigns"] == [] and body["recent_runs"] == []
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
