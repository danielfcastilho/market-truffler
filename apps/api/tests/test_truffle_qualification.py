"""Experimental Truffle qualification (app.ranking.qualification) and its
integration with live rankings and diagnostics (app.services.live_rankings)."""

import ast
import json
import shutil
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.domain.sniffer import SnifferFrameResult, SnifferInstrumentResult
from app.models.frame import MarketFrame as MarketFrameRow
from app.models.instrument import Instrument
from app.ranking import diagnostics, qualification
from app.ranking.qualification import (
    QualificationSpec,
    evaluate,
    load_qualification,
    qualification_hash,
)
from app.repositories.sniffer_repository import SnifferRepository
from app.services import live_rankings as live
from app.services.live_rankings import live_rankings, rank_direction

REPO = Path(__file__).resolve().parents[3]
RANKING = REPO / "research/oink_corp/ranking"
CANDIDATES = RANKING / "candidates"
QUALIFICATIONS = RANKING / "qualifications"
LONG, SHORT = "long-price-reversal-oi-v1", "short-price-reversal-oi-v1"
LONG_Q, SHORT_Q = "long-reversal-oi-truffle-v1", "short-reversal-oi-truffle-v1"
FRAME = datetime(2026, 10, 24, 20, 55, tzinfo=UTC)
NOW = FRAME + timedelta(minutes=2)
N = 60


def _board(*, c_oi_position: int = 11, shift: float = 0.0) -> dict[str, dict[str, float]]:
    """60 coins, C000 weakest 1h return. Long qualifiers by design: C001 and
    C002 (C002 exactly on both boundaries: return percentile 0.05, OI
    percentile 0.20). C003 ranks first (lowest OI, return percentile 0.067)
    but misses the 5% return tail."""
    oi_order = ["C003"] + [f"F{i}" for i in range(7)] + ["C001"]
    oi_order += [f"F{i}" for i in range(7, 7 + c_oi_position - 9)] + ["C002"]
    rest = [f"C{k:03d}" for k in range(4, N)] + ["C000"]
    symbols = [f"C{k:03d}" for k in range(N)]
    fillers = [s for s in rest if s not in oi_order]
    oi_rank = {s: i for i, s in enumerate(oi_order)}
    filler_iter = iter(fillers)
    for f in [s for s in oi_order if s.startswith("F")]:
        oi_rank[next(filler_iter)] = oi_rank.pop(f)
    for s in filler_iter:
        oi_rank[s] = len(oi_rank)
    assert sorted(oi_rank.values()) == list(range(N))
    return {
        s: {
            "return_1h": round(-0.06 + 0.002 * k + shift, 6),
            "oi_change_1h": round(-0.03 + 0.001 * oi_rank[s], 6),
            "relative_volume_1h": 1.0,
        }
        for k, s in enumerate(symbols)
    }


async def _seed(db_session, board, *, frame_time=FRAME, expected=None, analysis="live", ids=None):
    db_session.add(
        MarketFrameRow(
            frame_time=frame_time,
            expected_instrument_ids=[],
            expected_instruments=expected or len(board),
            available_instruments=len(board),
            status="complete" if (expected or len(board)) == len(board) else "partial",
            created_at=frame_time,
            finalized_at=frame_time + timedelta(seconds=30),
        )
    )
    if ids is None:
        now = datetime.now(UTC)
        rows = [
            Instrument(
                exchange="bybit",
                symbol=f"{s}USDT",
                base_coin=s,
                quote_coin="USDT",
                is_active=True,
                first_seen_at=now,
                last_seen_at=now,
            )
            for s in board
        ]
        db_session.add_all(rows)
        await db_session.commit()
        ids = {r.base_coin: r.id for r in rows}
    else:
        await db_session.commit()
    await SnifferRepository(db_session).save_result(
        SnifferFrameResult(
            frame_time=frame_time,
            analyzed_at=frame_time + timedelta(seconds=70),
            analysis=analysis,
            instruments=[
                SnifferInstrumentResult(
                    instrument_id=ids[s],
                    symbol=f"{s}USDT",
                    features={k: None if v is None else Decimal(str(v)) for k, v in values.items()},
                )
                for s, values in board.items()
            ],
        )
    )
    return ids


async def _rank(db_session, *, now=NOW, qualifications=QUALIFICATIONS, candidates=CANDIDATES, **kw):
    return await live_rankings(
        db_session,
        candidates_dir=candidates,
        long_candidate=kw.pop("long", LONG),
        short_candidate=kw.pop("short", SHORT),
        now=now,
        qualifications_dir=qualifications,
        long_qualification=kw.pop("long_q", LONG_Q),
        short_qualification=kw.pop("short_q", SHORT_Q),
        **kw,
    )


def _truffles(result, direction):
    return [t["symbol"] for t in result[direction]["qualification"]["truffles"]]


# -- specs ---------------------------------------------------------------------------------------


def test_the_configured_specs_are_valid_experimental_and_point_at_the_live_candidates():
    settings = get_settings()
    for qid, candidate, direction in (
        (settings.live_truffle_long_qualification, settings.live_ranking_long_candidate, "long"),
        (settings.live_truffle_short_qualification, settings.live_ranking_short_candidate, "short"),
    ):
        spec = load_qualification(QUALIFICATIONS, qid)
        assert (spec.ranking_candidate, spec.direction) == (candidate, direction)
        assert spec.status == "experimental" and spec.policy == "relative"


def test_the_spec_hash_is_stable_and_changes_with_any_threshold():
    spec = load_qualification(QUALIFICATIONS, LONG_Q)
    again = QualificationSpec.model_validate_json((QUALIFICATIONS / f"{LONG_Q}.json").read_text())
    assert qualification_hash(spec) == qualification_hash(again)
    raw = json.loads((QUALIFICATIONS / f"{LONG_Q}.json").read_text())
    raw["conditions"][0]["at_most"] = 0.06
    assert qualification_hash(QualificationSpec.model_validate(raw)) != qualification_hash(spec)


@pytest.mark.parametrize(
    "condition",
    [
        {"at_most": 0.1, "at_least": 0.0},  # two bounds
        {},  # no bound
        {"at_most": 1.0},  # a percentile bound must be inside (0, 1)
    ],
)
def test_a_condition_needs_exactly_one_meaningful_bound(condition):
    with pytest.raises(ValidationError):
        qualification.Condition.model_validate(
            {
                "id": "x",
                "feature": "return_1h",
                "measure": "percentile",
                "rationale": "r",
                **condition,
            }
        )


def test_missing_inputs_fail_as_missing_and_bounds_are_inclusive():
    spec = load_qualification(QUALIFICATIONS, LONG_Q)
    checks = evaluate(
        spec,
        ["A", "B"],
        {"return_1h": [0.0, -0.01], "oi_change_1h": [None, -0.02]},
    )
    a = {c["id"]: c for c in checks["A"]}
    assert a["return_fell"]["outcome"] == "passed"  # value 0.0 <= 0.0
    assert a["oi_tail"]["outcome"] == a["oi_fell"]["outcome"] == "missing"
    assert a["oi_tail"]["observed"] is None
    assert not qualification.qualified(checks["A"])
    # Percentiles are over the instruments with a value: B is alone (1.0).
    assert {c["id"]: c["observed"] for c in checks["B"]}["oi_tail"] == 1.0


# -- qualification on live rankings -------------------------------------------------------------


async def test_rank_one_can_fail_while_lower_ranked_coins_qualify(db_session):
    await _seed(db_session, _board())
    result = await _rank(db_session)
    long = result["long"]
    assert long["rows"][0]["symbol"] == "C003USDT"
    assert long["rows"][0]["qualification"] == {
        "qualified": False,
        "failed": ["return_tail"],
        "missing": [],
    }
    q = long["qualification"]
    assert q["status"] == "evaluated" and q["evaluated"] == N and q["qualified"] == 2
    assert _truffles(result, "long") == ["C001USDT", "C002USDT"]  # rank order
    [row] = (await _rank(db_session, symbol="C002USDT"))["long"]["rows"]
    boundary = {c["id"]: c for c in row["qualification"]["checks"]}
    assert all(c["outcome"] == "passed" for c in boundary.values())
    assert boundary["return_tail"]["observed"] == pytest.approx(0.05)
    assert boundary["oi_tail"]["observed"] == pytest.approx(0.2)


async def test_exactly_one_truffle(db_session):
    await _seed(db_session, _board(c_oi_position=12))  # C002 now just outside the OI tail
    result = await _rank(db_session)
    assert _truffles(result, "long") == ["C001USDT"]


async def test_zero_truffles_is_an_evaluated_result_not_a_failure(db_session):
    await _seed(db_session, _board(shift=0.2))  # every coin rose: no reversal of a fall
    result = await _rank(db_session)
    q = result["long"]["qualification"]
    assert (q["status"], q["qualified"], q["truffles"]) == ("evaluated", 0, [])
    assert all("return_fell" in r["qualification"]["failed"] for r in result["long"]["rows"])
    assert result["long"]["available"] and len(result["long"]["rows"]) == N


async def test_long_and_short_are_qualified_independently(db_session):
    await _seed(db_session, _board())
    result = await _rank(db_session)
    short = result["short"]["qualification"]
    assert short["spec"]["qualification_id"] == SHORT_Q
    # The strongest returns all have rising OI in this board: no Short Truffle,
    # while Long has two — neither side mirrors the other.
    assert short["qualified"] == 0 and result["long"]["qualification"]["qualified"] == 2
    assert result["truffles_in_both_directions"] == []


async def test_a_coin_qualifying_in_both_directions_is_kept_in_both(db_session, tmp_path):
    """The shipped specs make this impossible (a 1h return cannot be in both
    the weakest and the strongest 5%); a spec that allows it keeps both."""
    shutil.copytree(QUALIFICATIONS, tmp_path, dirs_exist_ok=True)
    raw = json.loads((QUALIFICATIONS / f"{SHORT_Q}.json").read_text())
    raw["conditions"] = [c for c in raw["conditions"] if c["feature"] == "oi_change_1h"]
    (tmp_path / f"{SHORT_Q}.json").write_text(json.dumps(raw))
    await _seed(db_session, _board())
    result = await _rank(db_session, qualifications=tmp_path)
    assert {"C001USDT", "C002USDT"} <= set(_truffles(result, "short"))
    assert result["truffles_in_both_directions"] == ["C001USDT", "C002USDT"]


async def test_a_stale_snapshot_never_shows_truffles(db_session):
    await _seed(db_session, _board())
    result = await _rank(db_session, now=FRAME + timedelta(minutes=8))
    q = result["long"]["qualification"]
    assert result["frame"]["stale"] and q["status"] == "withheld"
    assert q["truffles"] == [] and q["qualified"] is None and "stale" in q["reason"]
    assert all(r["qualification"] is None for r in result["long"]["rows"])
    # The same snapshot, fresh, still has its Truffles (the cache is not mutated).
    assert _truffles(await _rank(db_session), "long") == ["C001USDT", "C002USDT"]


async def test_an_incomplete_frame_withholds_qualification(db_session):
    await _seed(db_session, _board(), expected=70)  # 60 of 70 = 0.857 < 0.95
    q = (await _rank(db_session))["long"]["qualification"]
    assert q["status"] == "withheld" and "completeness" in q["reason"]
    gate = {c["id"]: c for c in q["frame_checks"]}
    assert gate["frame_completeness"]["outcome"] == "failed"
    assert gate["frame_fresh"]["outcome"] == "passed"


async def test_a_missing_or_mismatched_spec_is_unavailable_never_substituted(db_session, tmp_path):
    await _seed(db_session, _board())
    missing = await _rank(db_session, long_q="nope-v1")
    assert missing["long"]["qualification"]["status"] == "unavailable"
    assert missing["long"]["qualification"]["reason"] == "qualification nope-v1 not found"
    crossed = await _rank(db_session, long_q=SHORT_Q)
    assert "applies to short-price-reversal-oi-v1" in crossed["long"]["qualification"]["reason"]
    unconfigured = await _rank(db_session, qualifications=None)
    assert unconfigured["short"]["qualification"]["status"] == "unavailable"
    # Ranking itself is untouched by an unavailable qualification.
    assert unconfigured["long"]["rows"][0]["symbol"] == "C003USDT"


async def test_qualification_never_changes_scores_or_order(db_session):
    board = _board()
    await _seed(db_session, board)
    result = await _rank(db_session)
    symbols = [f"{s}USDT" for s in sorted(board)]
    features = {
        f: [board[s][f] for s in sorted(board)]
        for f in ("return_1h", "oi_change_1h", "relative_volume_1h")
    }
    for direction, candidate in (("long", LONG), ("short", SHORT)):
        plain = rank_direction(candidate, direction, CANDIDATES, symbols, features)["rows"]
        served = [
            {k: v for k, v in r.items() if k != "qualification"} for r in result[direction]["rows"]
        ]
        assert served == plain


async def test_the_board_carries_compact_flags_and_a_symbol_slice_the_full_explanation(db_session):
    await _seed(db_session, _board())
    board = await _rank(db_session)
    assert set(board["long"]["rows"][5]["qualification"]) == {"qualified", "failed", "missing"}
    one = await _rank(db_session, symbol="C003USDT")
    [row] = one["long"]["rows"]
    checks = {c["id"]: c for c in row["qualification"]["checks"]}
    assert checks["return_tail"] == {
        "id": "return_tail",
        "feature": "return_1h",
        "measure": "percentile",
        "threshold": {"at_most": 0.05},
        "observed": pytest.approx(4 / 60),
        "outcome": "failed",
    }
    assert one["long"]["qualification"]["truffles"] == board["long"]["qualification"]["truffles"]


async def test_results_are_deterministic(db_session):
    await _seed(db_session, _board())
    first = await _rank(db_session)
    live._SNAPSHOTS.clear()
    second = await _rank(db_session)
    assert json.dumps(first, default=str, sort_keys=True) == json.dumps(
        second, default=str, sort_keys=True
    )


# -- diagnostics on live rankings ---------------------------------------------------------------


async def test_diagnostics_identify_frame_model_and_contract(db_session):
    await _seed(db_session, _board())
    result = await _rank(db_session)
    diag = result["long"]["diagnostics"]
    assert diag["available"] and diag["eligible"] == N and diag["frame_time"] == FRAME
    assert diag["candidate_id"] == LONG
    assert diag["spec_hash"] == result["long"]["model"]["spec_hash"]
    assert diag["temporal_contract_version"] == 2
    assert diag["distribution"]["max"] == result["long"]["rows"][0]["score"]
    assert diag["limitations"]


async def test_stability_reports_insufficient_history_without_a_previous_frame(db_session):
    await _seed(db_session, _board())
    stability = (await _rank(db_session))["long"]["diagnostics"]["stability"]
    assert stability["available"] is False and "insufficient history" in stability["reason"]
    assert stability["previous_frame_time"] == FRAME - timedelta(minutes=5)


async def test_stability_against_the_previous_frame_of_the_same_model(db_session):
    ids = await _seed(db_session, _board(), frame_time=FRAME - timedelta(minutes=5))
    await _seed(db_session, _board(), ids=ids)
    stability = (await _rank(db_session))["long"]["diagnostics"]["stability"]
    assert stability["available"] and stability["top_k_overlap"] == 10
    assert stability["rank_correlation"] == pytest.approx(1.0)
    assert stability["spec_hash"] == (await _rank(db_session))["long"]["model"]["spec_hash"]


async def test_a_previous_frame_under_another_temporal_contract_is_not_compared(db_session):
    ids = await _seed(db_session, _board(), frame_time=FRAME - timedelta(minutes=5), analysis=None)
    await _seed(db_session, _board(), ids=ids)
    stability = (await _rank(db_session))["long"]["diagnostics"]["stability"]
    assert stability["available"] is False and "not comparable" in stability["reason"]


async def test_another_model_spec_is_scored_and_hashed_consistently(db_session, tmp_path):
    """A changed candidate is a different model: the current and previous
    frame are both ranked by it, and both carry its hash."""
    shutil.copytree(CANDIDATES, tmp_path, dirs_exist_ok=True)
    raw = json.loads((CANDIDATES / f"{LONG}.json").read_text())
    raw["terms"][1]["weight"] = -2.0
    (tmp_path / f"{LONG}.json").write_text(json.dumps(raw))
    ids = await _seed(db_session, _board(), frame_time=FRAME - timedelta(minutes=5))
    await _seed(db_session, _board(), ids=ids)
    original = await _rank(db_session)
    changed = await _rank(db_session, candidates=tmp_path)
    assert changed["long"]["model"]["spec_hash"] != original["long"]["model"]["spec_hash"]
    for result in (original, changed):
        diag = result["long"]["diagnostics"]
        assert (
            diag["spec_hash"]
            == diag["stability"]["spec_hash"]
            == result["long"]["model"]["spec_hash"]
        )


# -- API and boundaries -----------------------------------------------------------------------


async def test_the_endpoint_serves_diagnostics_and_qualification(
    client, test_user, db_session, monkeypatch
):
    await _seed(db_session, _board())
    monkeypatch.setattr(get_settings(), "research_ranking_dir", str(RANKING))
    await client.post(
        "/api/auth/login", json={"email": test_user.email, "password": "correct-horse-battery"}
    )
    body = (await client.get("/api/sniffer/rankings")).json()
    assert body["long"]["diagnostics"]["eligible"] == N
    assert body["long"]["qualification"]["spec"]["qualification_id"] == LONG_Q
    assert body["truffles_in_both_directions"] == []
    # Pre-existing consumers' fields are unchanged.
    assert body["long"]["rows"][0]["rank"] == 1 and "contributions" in body["long"]["rows"][0]


def test_diagnostics_and_qualification_cannot_reach_outcomes_research_or_the_database():
    for module in (diagnostics, qualification):
        source = Path(module.__file__).read_text()
        tree = ast.parse(source)
        imported = {
            n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
        } | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not any(
            m.startswith(("app.research", "app.repositories", "app.db", "sqlalchemy"))
            for m in imported
        ), module.__name__
        assert "forward_" not in source and "load_columns" not in source
