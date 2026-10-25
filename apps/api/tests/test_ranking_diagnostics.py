"""Descriptive ranking diagnostics (app.ranking.diagnostics)."""

import statistics

import pytest

from app.ranking import diagnostics as d


def test_distribution_statistics_are_correct():
    scores = [-1.5, -1.0, -0.5, 0.0, 0.5]
    out = d.score_distribution(scores)
    assert out["eligible"] == 5 and out["available"]
    assert (out["min"], out["max"], out["spread"]) == (-1.5, 0.5, 2.0)
    assert out["mean"] == pytest.approx(-0.5) and out["median"] == -0.5
    assert out["std"] == pytest.approx(statistics.pstdev(scores))
    assert out["percentiles"]["p25"] == pytest.approx(-1.0)
    assert out["percentiles"]["p90"] == pytest.approx(0.3)
    assert sum(out["histogram"]["counts"]) == 5
    assert out["histogram"]["counts"][0] == 1 and out["histogram"]["counts"][-1] == 1


def test_an_empty_universe_has_no_statistics():
    assert d.score_distribution([]) == {
        "eligible": 0,
        "available": False,
        "reason": "no eligible instruments",
    }
    assert d.separation([])["available"] is False


def test_a_single_instrument_reports_only_what_one_value_supports():
    out = d.score_distribution([-0.7])
    assert (out["min"], out["max"], out["std"], out["spread"]) == (-0.7, -0.7, 0.0, 0.0)
    assert set(out["percentiles"].values()) == {-0.7}
    sep = d.separation([-0.7])
    assert sep["top1_minus_top2"] is None and sep["top1_minus_top10"] is None
    assert sep["top_k_mean_minus_rest_mean"] is None and sep["near_top_count"] == 1


def test_equal_scores_are_a_flat_distribution_not_an_error():
    out = d.score_distribution([-1.0] * 60)
    assert out["std"] == 0 and out["spread"] == 0
    assert out["histogram"]["counts"][0] == 60
    sep = d.separation([-1.0] * 60)
    assert sep["top1_minus_top2"] == 0 and sep["near_top_count"] == 60


def test_separation_near_the_top():
    ranked = [-0.1, -0.2] + [-1.0] * 8 + [-1.4] * 50
    sep = d.separation(ranked)
    assert sep["top1_minus_top2"] == pytest.approx(0.1)
    assert sep["top1_minus_top10"] == pytest.approx(0.9)
    top_mean = (-0.1 - 0.2 - 8.0) / 10
    assert sep["top_k_mean_minus_rest_mean"] == pytest.approx(top_mean + 1.4)
    assert sep["near_top_count"] == 2  # within 10% of the 1.3 spread
    # Fewer than K: no top-10 comparison and no "rest".
    few = d.separation([-0.1, -0.2, -0.3])
    assert few["top1_minus_top10"] is None and few["top_k_mean_minus_rest_mean"] is None


def test_identical_rankings_are_perfectly_stable():
    board = [f"C{i:03d}" for i in range(60)]
    out = d.stability(board, board)
    assert out["top_k_overlap"] == 10 and out["entered_top_k"] == out["left_top_k"] == []
    assert all(c["change"] == 0 for c in out["top_k_rank_changes"])
    assert out["rank_correlation"] == pytest.approx(1.0)


def test_a_completely_changed_top_k():
    before = [f"C{i:03d}" for i in range(60)]
    now = before[10:] + before[:10]
    out = d.stability(now, before)
    assert out["top_k_overlap"] == 0
    assert out["entered_top_k"] == before[10:20] and out["left_top_k"] == before[:10]
    assert out["top_k_rank_changes"][0] == {
        "symbol": "C010",
        "rank": 1,
        "previous_rank": 11,
        "change": 10,
    }
    reversed_out = d.stability(before[::-1], before)
    assert reversed_out["rank_correlation"] == pytest.approx(-1.0)


def test_newcomers_have_no_previous_rank_and_correlation_uses_common_coins():
    before = ["A", "B", "C", "D"]
    now = ["NEW", "A", "B", "C"]
    out = d.stability(now, before, k=2)
    assert out["top_k_rank_changes"][0] == {
        "symbol": "NEW",
        "rank": 1,
        "previous_rank": None,
        "change": None,
    }
    assert out["common_instruments"] == 3 and out["rank_correlation"] == pytest.approx(1.0)


def test_diagnostics_are_deterministic():
    scores = [((i * 37) % 101) / -50 for i in range(300)]
    ranked = sorted(scores, reverse=True)
    assert d.score_distribution(scores) == d.score_distribution(list(reversed(scores)))
    assert d.separation(ranked) == d.separation(list(ranked))
