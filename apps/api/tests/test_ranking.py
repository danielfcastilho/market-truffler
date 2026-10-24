"""Ranking research — synthetic data only."""

import json
import math
from datetime import UTC, date, datetime, timedelta

import pyarrow as pa
import pytest
from research_helpers import write_dataset

from app.research import ranking
from app.research.experiment import ExperimentSpecError
from app.research.ranking import (
    RankingCandidate,
    RankingStudy,
    candidate_hash,
    decision_universes,
    evaluate_study,
    rank_timestamp,
    transform,
)
from app.scripts import run_ranking_study

LABEL = "forward_relative_return_1h"
START = date(2026, 9, 1)


def _candidate(cid="long-price-v1", direction="long", terms=None, **overrides) -> dict:
    return {
        "spec_version": 1,
        "candidate_id": cid,
        "name": "Long — price only",
        "model_family": "linear_weighted_v1",
        "direction": direction,
        "objective": "o",
        "terms": terms if terms is not None else PRICE,
        "missing_policy": "exclude",
        **overrides,
    }


PRICE = [{"feature": "return_1h", "transform": "cs_percentile", "weight": -1.0}]
PRICE_OI = PRICE + [{"feature": "oi_change_1h", "transform": "cs_percentile", "weight": -1.0}]


def _rows(days: int, bonus: float, *, missing_oi_for: str | None = None):
    """60 coins per 15-minute timestamp, 20 per day: return ranks rotate,
    OI falls for odd ranks, and the next-hour relative return is raised by
    `bonus` where OI fell (plus a day-level wobble of alternating sign)."""
    rows = []
    for d in range(days):
        wobble = 0.001 if d % 2 else -0.001
        day = START + timedelta(days=d)
        for i in range(20):
            t = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(minutes=15 * i)
            for k in range(60):
                rank = (k + i + d) % 60
                oi = -0.02 if rank % 2 else 0.03
                symbol = f"S{k:03d}"
                rows.append(
                    {
                        "frame_time": t,
                        "symbol": symbol,
                        "return_1h": float(rank),
                        "oi_change_1h": None if symbol == missing_oi_for else oi,
                        LABEL: (bonus + wobble) if oi < 0 else 0.0,
                    }
                )
    return rows


def _lab(tmp_path, days=14, bonus=0.01, **kwargs):
    data = tmp_path / "data"
    manifest = write_dataset(data / "dev", _rows(days, bonus, **kwargs), sample_interval_minutes=15)
    root = tmp_path / "ranking"
    (root / "candidates").mkdir(parents=True)
    (root / "studies").mkdir()
    (root / "candidates" / "long-price-v1.json").write_text(json.dumps(_candidate()))
    (root / "candidates" / "long-price-oi-v1.json").write_text(
        json.dumps(_candidate("long-price-oi-v1", terms=PRICE_OI, name="Long — price + OI"))
    )
    study = {
        "spec_version": 1,
        "study_id": "long-oi-vs-price-v1",
        "name": "Long: does OI improve a price ranking?",
        "question": "q",
        "candidate": "long-price-oi-v1",
        "reference": "long-price-v1",
        "datasets": [{"name": "dev", "data_sha256": manifest["data_sha256"]}],
        "label": LABEL,
        "top_k": 10,
    }
    path = root / "studies" / "long-oi-vs-price-v1.json"
    path.write_text(json.dumps(study))
    return path, data


# -- specifications ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"terms": [{"feature": "return_1h", "transform": "cs_percentile", "weight": 0}]},
        {"terms": [{"feature": "return_1h", "transform": "cs_percentile", "weight": math.inf}]},
        {"terms": [{"feature": "return_1h", "transform": "log", "weight": 1}]},
        {"terms": PRICE + PRICE},
        {"model_family": "gradient_boosting"},
        {"missing_policy": "impute_zero"},
        {"direction": "both"},
        {"terms": []},
    ],
)
def test_invalid_candidates_are_rejected(overrides):
    with pytest.raises(ValueError):
        RankingCandidate.model_validate(_candidate(**overrides))


def test_studies_use_relative_labels_and_hash_stably():
    with pytest.raises(ValueError, match="market-relative"):
        RankingStudy.model_validate(
            {
                "spec_version": 1,
                "study_id": "s-v1",
                "name": "n",
                "question": "q",
                "candidate": "c-v1",
                "datasets": [{"name": "d", "data_sha256": "0" * 64}],
                "label": "forward_return_1h",
                "top_k": 5,
            }
        )
    a = RankingCandidate.model_validate(_candidate())
    b = RankingCandidate.model_validate(_candidate(terms=PRICE_OI))
    assert candidate_hash(a) == candidate_hash(RankingCandidate.model_validate(_candidate()))
    assert candidate_hash(a) != candidate_hash(b)


def test_unsupported_features_and_mismatched_directions_are_refused(tmp_path):
    path, data = _lab(tmp_path, days=1)
    cand_dir = path.parent.parent / "candidates"
    (cand_dir / "long-price-oi-v1.json").write_text(
        json.dumps(
            _candidate(
                "long-price-oi-v1",
                terms=[{"feature": "magic_1h", "transform": "identity", "weight": 1}],
            )
        )
    )
    study, cand, ref = ranking.load_study(path, cand_dir)
    with pytest.raises(ExperimentSpecError, match="unknown features"):
        evaluate_study(study, cand, ref, data, seed=1)
    (cand_dir / "long-price-oi-v1.json").write_text(
        json.dumps(_candidate("long-price-oi-v1", direction="short"))
    )
    with pytest.raises(ExperimentSpecError, match="same direction"):
        ranking.load_study(path, cand_dir)


# -- ranking construction ----------------------------------------------------------------------


def test_transforms_handle_ties_and_zero_variance_deterministically():
    assert transform("cs_percentile", [3.0, 1.0, 3.0, 2.0]) == ([1.0, 0.25, 1.0, 0.5], False)
    assert transform("cs_percentile", [5.0, 5.0]) == ([1.0, 1.0], True)
    z, flat = transform("cs_robust_z", [1.0, 2.0, 3.0, 4.0, 100.0])
    assert not flat and z[2] == 0.0 and z[4] > z[3]
    assert transform("cs_robust_z", [2.0, 2.0, 2.0, 9.0]) == ([0.0, 0.0, 0.0, 0.0], True)
    assert transform("identity", [1.5, -2.0]) == ([1.5, -2.0], False)


def test_scores_are_deterministic_with_symbol_tie_breaking():
    candidate = RankingCandidate.model_validate(_candidate())
    t = datetime(2026, 9, 1, tzinfo=UTC)
    ranked = rank_timestamp(candidate, t, ["B", "A", "C"], {"return_1h": [1.0, 1.0, 5.0]})
    assert ranked.symbols == ["A", "B", "C"]  # equal scores: symbol order
    assert ranked.scores == [-2 / 3, -2 / 3, -1.0]


def test_the_decision_universe_needs_every_feature_finite_and_no_label():
    table = pa.table(
        {
            "frame_time": [datetime(2026, 9, 1, tzinfo=UTC)] * 4,
            "symbol": ["A", "B", "C", "D"],
            "return_1h": [1.0, None, 2.0, float("nan")],
            "oi_change_1h": [0.1, 0.2, None, 0.3],
        }
    )
    [(_, symbols, features, rows)] = decision_universes(table, ["return_1h", "oi_change_1h"])
    assert symbols == ["A"] and rows == [0] and features["oi_change_1h"] == [0.1]


def test_rankings_do_not_depend_on_outcomes(tmp_path):
    path, data = _lab(tmp_path, days=14)
    study, cand, ref = ranking.load_study(path, path.parent.parent / "candidates")
    a, _ = evaluate_study(study, cand, ref, data, seed=1)
    path2, data2 = _lab(tmp_path / "other", days=14, bonus=-0.01)
    study2, cand2, ref2 = ranking.load_study(path2, path2.parent.parent / "candidates")
    b, _ = evaluate_study(study2, cand2, ref2, data2, seed=1)
    # Same features, opposite outcomes: same coverage, opposite evidence.
    assert a["coverage"] == b["coverage"]
    assert a["candidate"]["distinct_top_symbols"] == b["candidate"]["distinct_top_symbols"]
    assert a["primary"]["mean"] == pytest.approx(-b["primary"]["mean"])


# -- evaluation -------------------------------------------------------------------------------


def test_top_k_excess_utility_is_exact_for_long_and_short(monkeypatch):
    monkeypatch.setattr(ranking, "MIN_UNIVERSE", 2)
    t = datetime(2026, 9, 1, tzinfo=UTC)
    universes = [(t, ["A", "B", "C", "D"], {"return_1h": [1.0, 2.0, 3.0, 4.0]}, [0, 1, 2, 3])]
    utility = [0.04, 0.0, 0.0, -0.04]
    long = RankingCandidate.model_validate(_candidate())  # favours low return: A first
    series, skipped = ranking._evaluate_model(long, universes, utility, 1)
    assert skipped == 0 and series.q[t] == pytest.approx(0.04 - 0.0)
    assert series.separation[t] == pytest.approx(0.08)
    short = RankingCandidate.model_validate(
        _candidate(
            direction="short",
            terms=[{"feature": "return_1h", "transform": "cs_percentile", "weight": 1.0}],
        )
    )
    short_utility = [-u for u in [0.04, 0.0, 0.0, -0.04]]  # Short utility = -R_rel
    series, _ = ranking._evaluate_model(short, universes, short_utility, 1)
    assert series.q[t] == pytest.approx(0.04)  # D ranked first, fell 4%: good Short


def test_a_feature_that_carries_information_improves_the_ranking(tmp_path):
    path, data = _lab(tmp_path, days=14)
    study, cand, ref = ranking.load_study(path, path.parent.parent / "candidates")
    report, _ = evaluate_study(study, cand, ref, data, seed=7)
    primary = report["primary"]
    assert report["segment"] == "development" and report["exploratory"] is True
    assert primary["matched_timestamps"] == 200  # 10 development days x 20
    assert primary["verdict"] == "positive_evidence" and primary["uncertainty"]["interval"][0] > 0
    assert (
        report["candidate"]["mean_top_k_excess_utility"]
        > report["reference"]["mean_top_k_excess_utility"]
    )
    # Paired differences are exactly candidate minus reference per timestamp.
    t0 = next(iter(primary["paired_differences"]))
    assert len(primary["paired_differences"]) == primary["matched_timestamps"]
    assert isinstance(primary["paired_differences"][t0], float)


def test_an_uninformative_addition_is_not_evidence(tmp_path):
    path, data = _lab(tmp_path, days=14, bonus=0.0)
    study, cand, ref = ranking.load_study(path, path.parent.parent / "candidates")
    report, _ = evaluate_study(study, cand, ref, data, seed=7)
    assert report["verdict"] == "inconclusive"


def test_few_days_are_insufficient_and_uncertainty_is_suppressed(tmp_path):
    path, data = _lab(tmp_path, days=2)
    study, cand, ref = ranking.load_study(path, path.parent.parent / "candidates")
    report, _ = evaluate_study(study, cand, ref, data, seed=7)
    assert report["verdict"] == "insufficient_data"
    assert report["primary"]["uncertainty"]["suppressed"] is True
    assert any("qualifying days" in r for r in report["verdict_reasons"])


def test_both_models_rank_the_same_universe(tmp_path):
    # S000 lacks OI: it is out of the universe for the price-only reference too.
    path, data = _lab(tmp_path, days=2, missing_oi_for="S000")
    study, cand, ref = ranking.load_study(path, path.parent.parent / "candidates")
    report, _ = evaluate_study(study, cand, ref, data, seed=7)
    assert report["coverage"]["mean_universe"] == 59.0
    assert report["primary"]["matched_timestamps"] == report["candidate"]["timestamps"]


# -- runs and boundaries ------------------------------------------------------------------------


def test_runs_are_immutable_reproducible_and_repeats_are_marked(tmp_path):
    path, data = _lab(tmp_path, days=14)
    first_dir, first = run_ranking_study.run(path, data)
    second_dir, second = run_ranking_study.run(path, data)
    assert first_dir != second_dir and first["status"] == "completed"
    assert first["report"] == second["report"]  # deterministic, same seed
    assert first["repeat_of"] == [] and second["repeat_of"] == [
        f"long-oi-vs-price-v1/{first['run_id']}"
    ]
    assert (
        first["kind"] == "ranking_study" and first["reference"]["candidate_id"] == "long-price-v1"
    )
    study = json.loads(path.read_text())
    study["top_k"] = 5
    path.write_text(json.dumps(study))
    with pytest.raises(ExperimentSpecError, match="immutable"):
        run_ranking_study.run(path, data)


def test_reserved_prospective_data_cannot_be_ranked(tmp_path):
    path, data = _lab(tmp_path, days=2)
    for role, start, end in (
        ("validation", "2026-09-02", "2026-09-10"),
        ("holdout", "2026-09-01", "2026-09-02"),
    ):
        directory = data / "evaluations" / f"reg-{role}"
        (directory / "registration").mkdir(parents=True)
        (directory / "events").mkdir()
        (directory / "registration" / "registration.json").write_text(
            json.dumps(
                {
                    "registration": {
                        "registration_id": f"reg-{role}",
                        "partitions": [{"role": role, "start": start, "end": end}],
                    }
                }
            )
        )
        _, record = run_ranking_study.run(path, data)
        assert record["status"] == "failed" and f"reserved for the {role}" in record["error"]
        assert "report" not in record
        (directory / "registration" / "registration.json").unlink()
        (directory / "registration").rmdir()
        (directory / "events").rmdir()
        directory.rmdir()


def test_the_feature_evaluator_is_untouched():
    from app.research import evaluation

    assert evaluation.EVALUATOR_VERSION == 1
    assert ranking.RANKING_EVALUATOR_VERSION == 1
    assert ranking.MIN_UNIVERSE == evaluation.MIN_UNIVERSE_PER_TIMESTAMP
