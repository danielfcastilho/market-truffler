from datetime import UTC, date, datetime, timedelta

import pytest
from research_helpers import at, daily_grid

from app.research.evaluation import SplitPlan, evaluate, plan_split
from app.research.experiment import Observations

NO_REUSE = {"independent": True}


def _obs(rows: list[tuple[datetime, str, bool, float | None]]) -> Observations:
    """rows: (frame_time, symbol, selected, outcome-or-None)."""
    return Observations(
        frame_time=[r[0] for r in rows],
        symbol=[r[1] for r in rows],
        selected=[r[2] for r in rows],
        eligible=[r[3] is not None for r in rows],
        outcome=[r[3] for r in rows],
        timestamps_percentile_universe_too_small=0,
    )


def _dev_split(*days: date) -> SplitPlan:
    return SplitPlan(list(days), [], list(days), [], None)


def _evaluate(obs, split, segment="development", horizon=60, interval=5, seed=1):
    return evaluate(
        obs,
        segment=segment,
        split=split,
        horizon_minutes=horizon,
        sample_interval_minutes=interval,
        seed=seed,
        validation_reuse=NO_REUSE,
    )


# -- split policy --------------------------------------------------------------------------


def _dataset_times(n_days: int, timestamps_per_day: int = 12, universe: int = 50):
    return [
        t
        for day in range(1, n_days + 1)
        for t in daily_grid(day, timestamps_per_day)
        for _ in range(universe)
    ]


@pytest.mark.parametrize(
    ("n_days", "n_dev", "n_val"),
    [(1, 1, 0), (5, 5, 0), (6, 3, 3), (8, 5, 3), (9, 6, 3), (12, 8, 4), (30, 20, 10)],
)
def test_split_assigns_the_latest_max_3_or_a_third_of_days_to_validation(n_days, n_dev, n_val):
    plan = plan_split(_dataset_times(n_days))
    assert (len(plan.development_days), len(plan.validation_days)) == (n_dev, n_val)
    assert plan.development_days + plan.validation_days == plan.qualifying_days  # chronological
    if n_val:
        assert plan.purge_boundary == datetime.combine(
            plan.validation_days[0], datetime.min.time(), tzinfo=UTC
        )
    else:
        assert plan.purge_boundary is None


def test_days_qualify_on_dataset_availability_only():
    """Too few timestamps, or timestamps with too small a universe, don't
    make a qualifying day — and nothing about any hypothesis is consulted
    (`plan_split` only ever sees dataset timestamps)."""
    times = _dataset_times(6)
    times += [t for t in daily_grid(7, 11) for _ in range(50)]  # 11 timestamps: too few
    times += [t for t in daily_grid(8, 20) for _ in range(49)]  # universe 49: too small
    plan = plan_split(times)
    assert [d.day for d in plan.excluded_days] == [7, 8]
    assert len(plan.qualifying_days) == 6


# -- baseline and spread ----------------------------------------------------------------


def test_spread_matches_a_hand_computation():
    t1, t2, t3 = at(1, 0, 0), at(1, 0, 5), at(1, 0, 10)
    obs = _obs(
        [
            # t1: selected A=0.03; universe {0.03, 0.01, -0.01} -> mean 0.01; spread 0.02
            (t1, "A", True, 0.03), (t1, "B", False, 0.01), (t1, "C", False, -0.01),
            # t2: selected A=-0.02, B has no outcome; universe {-0.02, 0.0} -> spread -0.01
            (t2, "A", True, -0.02), (t2, "B", True, None), (t2, "C", False, 0.0),
            # t3: nothing selected -> no spread for t3
            (t3, "A", False, 0.05), (t3, "B", False, 0.05),
        ]
    )  # fmt: skip
    report = _evaluate(obs, _dev_split(date(2026, 9, 1)))

    assert report["spread"]["mean"] == pytest.approx((0.02 + -0.01) / 2)
    counts = report["counts"]
    assert counts["selected_at_decision"] == 3
    assert counts["selected_with_outcome"] == 2
    assert counts["selected_missing_outcome"] == 1
    assert counts["eligible_universe_rows"] == 7
    assert counts["universe_rows_excluded"] == 1
    assert counts["timestamps"] == 3
    assert counts["timestamps_with_selection"] == 2
    assert counts["timestamps_without_selection"] == 1
    assert report["outcomes"]["selected_mean"] == pytest.approx(0.005)
    assert report["outcomes"]["baseline_mean"] == pytest.approx(0.11 / 7)
    assert report["spread"]["daily"][0]["timestamps_with_selection"] == 2


def test_selecting_the_whole_eligible_universe_gives_zero_spread():
    obs = _obs(
        [(at(1, 0, 5 * i), f"S{k}", True, (k - i) / 100) for i in range(3) for k in range(5)]
    )
    assert _evaluate(obs, _dev_split(date(2026, 9, 1)))["spread"]["mean"] == pytest.approx(0)


def test_timestamps_are_weighted_equally_not_by_row_count():
    t1, t2 = at(1, 0, 0), at(1, 0, 5)
    rows = [(t1, "A", True, 0.10), (t1, "B", False, 0.0)]  # spread 0.05
    rows += [(t2, f"S{k}", k < 9, 0.0) for k in range(10)]  # 9 selected, spread 0.0
    report = _evaluate(_obs(rows), _dev_split(date(2026, 9, 1)))
    assert report["spread"]["mean"] == pytest.approx(0.025)  # not row-weighted (0.005)


def test_short_and_long_spreads_are_symmetric():
    rows = [(at(1, 0, 5 * i), f"S{k}", k == 0, (k + i) / 100) for i in range(3) for k in range(4)]
    long = _evaluate(_obs(rows), _dev_split(date(2026, 9, 1)))["spread"]["mean"]
    flipped = [(t, s, sel, -o) for t, s, sel, o in rows]
    short = _evaluate(_obs(flipped), _dev_split(date(2026, 9, 1)))["spread"]["mean"]
    assert short == pytest.approx(-long)


# -- segments and purge ------------------------------------------------------------------


def test_development_rows_whose_label_crosses_into_validation_are_purged():
    boundary = datetime(2026, 9, 4, tzinfo=UTC)
    split = SplitPlan(
        [date(2026, 9, d) for d in range(1, 7)],
        [],
        [date(2026, 9, d) for d in (1, 2, 3)],
        [date(2026, 9, d) for d in (4, 5, 6)],
        boundary,
    )
    exactly = boundary - timedelta(hours=1)  # label ends exactly at the boundary: kept
    crossing = exactly + timedelta(minutes=5)  # ends 5 minutes into validation: purged
    rows = [(exactly, "A", True, 0.01), (exactly, "B", False, 0.0)]
    rows += [(crossing, "A", True, 0.5), (crossing, "B", False, 0.0)]
    rows += [(at(4, 1), "A", True, 0.2), (at(4, 1), "B", False, 0.0)]  # validation rows

    dev = _evaluate(_obs(rows), split, "development")
    assert dev["purged_rows"] == 2
    assert dev["counts"]["timestamps"] == 1
    assert dev["spread"]["mean"] == pytest.approx(0.005)

    val = _evaluate(_obs(rows), split, "validation")
    assert val["days"] == ["2026-09-04", "2026-09-05", "2026-09-06"]
    assert val["purged_rows"] == 0
    assert val["spread"]["mean"] == pytest.approx(0.1)


def test_validation_without_a_validation_segment_is_insufficient_data():
    report = _evaluate(_obs([]), _dev_split(date(2026, 9, 1)), "validation")
    assert report["verdict"] == "insufficient_data"
    assert "no validation segment" in report["verdict_reasons"][0]


# -- uncertainty and verdicts --------------------------------------------------------------


def _many_days(n_days: int, selected_edge: float, *, symbols: int = 60, per_day: int = 20):
    """Each timestamp selects 6 rotating symbols (so 60 distinct symbols
    and a 10% top-1 share); selected outcomes = edge + small day-specific
    noise, everything else 0."""
    rows = []
    for day in range(1, n_days + 1):
        noise = ((day * 7) % 5 - 2) / 10_000
        for i, t in enumerate(daily_grid(day, per_day)):
            chosen = {(i * 6 + j) % symbols for j in range(6)}
            for k in range(symbols):
                outcome = selected_edge + noise if k in chosen else 0.0
                rows.append((t, f"S{k:02d}", k in chosen, outcome))
    return _obs(rows), _dev_split(*(date(2026, 9, d) for d in range(1, n_days + 1)))


def test_few_days_suppress_the_interval_and_yield_insufficient_data():
    obs, split = _many_days(9, 0.01)
    report = _evaluate(obs, split)
    assert report["uncertainty"]["suppressed"] is True
    assert report["verdict"] == "insufficient_data"
    assert any("qualifying days" in r for r in report["verdict_reasons"])


def test_a_consistent_positive_spread_over_enough_days_is_positive_evidence():
    obs, split = _many_days(10, 0.01)
    report = _evaluate(obs, split)
    assert report["uncertainty"]["suppressed"] is False
    low, high = report["uncertainty"]["interval"]
    assert 0 < low <= report["spread"]["mean"] <= high
    assert report["verdict"] == "positive_evidence"


def test_a_consistent_negative_spread_is_negative_evidence():
    obs, split = _many_days(10, -0.01)
    assert _evaluate(obs, split)["verdict"] == "negative_evidence"


def test_a_spread_indistinguishable_from_zero_is_inconclusive():
    obs, split = _many_days(10, 0.0)
    assert _evaluate(obs, split)["verdict"] == "inconclusive"


def test_symbol_concentration_blocks_positive_evidence():
    """Every timestamp selects S00 plus one rotating symbol: 30 distinct
    selected symbols (enough), but S00 is half of all selections."""
    rows = []
    for day in range(1, 11):
        for i, t in enumerate(daily_grid(day, 20)):
            chosen = {0, 1 + (day * 20 + i) % 29}
            rows += [(t, f"S{k:02d}", k in chosen, 0.01 if k in chosen else 0.0) for k in range(60)]
    split = _dev_split(*(date(2026, 9, d) for d in range(1, 11)))
    report = _evaluate(_obs(rows), split)
    assert report["concentration"]["top1_share"] == pytest.approx(0.5)
    assert report["counts"]["distinct_selected_symbols"] == 30
    assert report["verdict"] == "inconclusive"


def test_the_bootstrap_is_deterministic_for_a_seed():
    obs, split = _many_days(10, 0.01)
    assert _evaluate(obs, split, seed=7) == _evaluate(obs, split, seed=7)
    assert (
        _evaluate(obs, split, seed=7)["uncertainty"] != _evaluate(obs, split, seed=8)["uncertainty"]
    )


def test_every_report_carries_the_disclosures():
    obs, split = _many_days(10, 0.01)
    disclosures = " ".join(_evaluate(obs, split)["disclosures"])
    for phrase in ("none is proof", "costs", "overlap", "dependence", "survivorship", "regimes"):
        assert phrase in disclosures
