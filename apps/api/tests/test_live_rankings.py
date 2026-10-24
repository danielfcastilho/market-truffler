"""Experimental live rankings: frame selection, configured candidates,
explainable scores, freshness, and parity with historical ranking."""

import ast
import json
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pytest

from app.core.config import get_settings
from app.domain.sniffer import SnifferFrameResult, SnifferInstrumentResult
from app.models.frame import MarketFrame as MarketFrameRow
from app.models.instrument import Instrument
from app.ranking.scoring import load_candidate_file
from app.repositories.sniffer_repository import SnifferRepository
from app.research.ranking import decision_universes, rank_timestamp
from app.services import live_rankings as live
from app.services.live_rankings import live_rankings, rank_direction

REPO = Path(__file__).resolve().parents[3]
CANDIDATES = REPO / "research/oink_corp/ranking/candidates"
LONG, SHORT = "long-price-reversal-oi-v1", "short-price-reversal-oi-v1"
FRAME = datetime(2026, 10, 24, 20, 55, tzinfo=UTC)
NOW = FRAME + timedelta(minutes=1)


def _features(k: int) -> dict[str, Decimal | None]:
    """Coin k: returns spread over a range with ties; OI missing for k=0."""
    return {
        "return_1h": Decimal(str(round((k % 30 - 15) / 1000, 4))),
        "oi_change_1h": None if k == 0 else Decimal(str(round(((k * 7) % 11 - 5) / 100, 4))),
        "relative_volume_1h": Decimal("1.5"),
        "return_5m": Decimal("0.001"),
    }


async def _seed(db_session, *, coins=60, status="complete", frame_time=FRAME):
    db_session.add(
        MarketFrameRow(
            frame_time=frame_time,
            expected_instrument_ids=[],
            expected_instruments=coins,
            available_instruments=coins,
            status=status,
            created_at=frame_time,
            finalized_at=None if status == "building" else frame_time,
        )
    )
    now = datetime.now(UTC)
    instruments = [
        Instrument(
            exchange="bybit",
            symbol=f"C{k:03d}USDT",
            base_coin=f"C{k:03d}",
            quote_coin="USDT",
            is_active=True,
            first_seen_at=now,
            last_seen_at=now,
        )
        for k in range(coins)
    ]
    db_session.add_all(instruments)
    await db_session.commit()
    await SnifferRepository(db_session).save_result(
        SnifferFrameResult(
            frame_time=frame_time,
            analyzed_at=frame_time + timedelta(seconds=6),
            instruments=[
                SnifferInstrumentResult(instrument_id=i.id, symbol=i.symbol, features=_features(k))
                for k, i in enumerate(instruments)
            ],
        )
    )


async def _rank(db_session, *, now=NOW, long=LONG, short=SHORT, candidates=CANDIDATES):
    return await live_rankings(
        db_session, candidates_dir=candidates, long_candidate=long, short_candidate=short, now=now
    )


# -- frame selection and availability ------------------------------------------------------------


async def test_nothing_analyzed_yet_is_unavailable(db_session):
    result = await _rank(db_session)
    assert result == {"available": False, "reason": "Sniffer has not analyzed any frame yet."}


async def test_an_unfinalized_frame_is_never_ranked(db_session):
    await _seed(db_session, status="building")
    result = await _rank(db_session)
    assert result["available"] is False and "has not finalized" in result["reason"]


async def test_both_directions_rank_the_same_completed_frame(db_session):
    await _seed(db_session)
    result = await _rank(db_session)
    assert result["available"] and result["frame"]["frame_time"] == FRAME
    assert result["frame"]["status"] == "complete" and result["frame"]["stale"] is False
    long, short = result["long"], result["short"]
    assert long["available"] and short["available"]
    assert long["model"]["candidate_id"] == LONG and short["model"]["candidate_id"] == SHORT
    assert long["model"]["direction"] == "long" and short["model"]["direction"] == "short"
    assert "not independently validated" in long["model"]["validation_status"]
    # C000 lacks Open Interest: excluded from both, explicitly.
    assert long["eligible"] == short["eligible"] == 59
    assert long["excluded"] == [{"symbol": "C000USDT", "missing": ["oi_change_1h"]}]
    assert {r["symbol"] for r in long["rows"]} == {r["symbol"] for r in short["rows"]}


async def test_rows_are_ordered_by_score_then_symbol_and_contributions_reconcile(db_session):
    await _seed(db_session)
    rows = (await _rank(db_session))["long"]["rows"]
    keys = [(-r["score"], r["symbol"]) for r in rows]
    assert keys == sorted(keys) and [r["rank"] for r in rows] == list(range(1, 60))
    for row in rows:
        total = 0.0
        for c in row["contributions"]:
            assert c["contribution"] == c["weight"] * c["transformed"]
            total += c["contribution"]
        assert total == row["score"]  # exactly, same summation order
    top = rows[0]
    assert top["context"]["relative_volume_1h"] == 1.5
    assert [c["feature"] for c in top["contributions"]] == ["return_1h", "oi_change_1h"]


async def test_a_missing_or_invalid_candidate_is_unavailable_never_substituted(
    db_session, tmp_path
):
    await _seed(db_session)
    (tmp_path / f"{LONG}.json").write_text((CANDIDATES / f"{LONG}.json").read_text())
    (tmp_path / "broken-v1.json").write_text("{}")
    (tmp_path / "wrong-v1.json").write_text(
        (CANDIDATES / f"{LONG}.json").read_text().replace(LONG, "wrong-v1")
    )
    result = await _rank(db_session, candidates=tmp_path, short="missing-v1")
    assert result["long"]["available"] and result["short"]["available"] is False
    assert result["short"]["reason"] == "candidate missing-v1 not found"
    assert result["short"]["rows"] == []
    assert (
        "invalid"
        in (await _rank(db_session, candidates=tmp_path, short="broken-v1"))["short"]["reason"]
    )
    wrong = await _rank(db_session, candidates=tmp_path, short="wrong-v1")
    assert wrong["short"]["reason"] == "candidate wrong-v1 ranks long, not short"


async def test_too_small_a_cross_section_is_not_ranked(db_session):
    await _seed(db_session, coins=20)
    result = await _rank(db_session)
    assert result["long"]["available"] is False and "(< 50)" in result["long"]["reason"]


async def test_staleness_follows_the_frame_cadence(db_session):
    await _seed(db_session)
    late = await _rank(db_session, now=FRAME + timedelta(minutes=8))
    assert late["frame"]["stale"] and "should have finalized" in late["frame"]["stale_reason"]
    newer = FRAME + timedelta(minutes=5)
    db_session.add(
        MarketFrameRow(
            frame_time=newer,
            expected_instrument_ids=[],
            expected_instruments=60,
            available_instruments=60,
            status="complete",
            created_at=newer,
            finalized_at=newer + timedelta(seconds=30),
        )
    )
    await db_session.commit()
    # Sniffer normally analyzes within seconds: not stale during that window.
    analyzing = await _rank(db_session, now=newer + timedelta(seconds=40))
    assert analyzing["frame"]["stale"] is False
    behind = await _rank(db_session, now=newer + timedelta(minutes=3))
    assert behind["frame"]["frame_time"] == FRAME  # never silently another frame
    assert "Sniffer has not analyzed it" in behind["frame"]["stale_reason"]


# -- API -------------------------------------------------------------------------------------


async def test_the_endpoint_requires_authentication_and_serves_one_snapshot(
    client, test_user, db_session, monkeypatch
):
    assert (await client.get("/api/sniffer/rankings")).status_code == 401
    await _seed(db_session)
    monkeypatch.setattr(get_settings(), "research_ranking_dir", str(CANDIDATES.parent))
    response = await client.post(
        "/api/auth/login", json={"email": test_user.email, "password": "correct-horse-battery"}
    )
    assert response.status_code == 200
    body = (await client.get("/api/sniffer/rankings")).json()
    assert body["available"] and body["frame"]["analyzed_instruments"] == 60
    assert body["long"]["rows"][0]["rank"] == 1 and body["short"]["model"]["candidate_id"] == SHORT


# -- historical/live parity ------------------------------------------------------------------------


@pytest.mark.parametrize("candidate_id", [LONG, SHORT, "long-price-reversal-v1"])
def test_live_and_historical_rankings_are_identical(candidate_id):
    """Same candidate, same features, same universe: the research path
    (dataset table -> decision universe -> rank_timestamp) and the live path
    (Sniffer features -> rank_direction) agree exactly."""
    candidate = load_candidate_file(CANDIDATES / f"{candidate_id}.json")
    symbols = [f"C{k:03d}USDT" for k in range(80)]
    raw = {
        "return_1h": [float((k % 25 - 12) / 1000) for k in range(80)],
        "oi_change_1h": [
            None if k % 17 == 0 else math.nan if k == 5 else float((k * 7) % 11 - 5) / 100
            for k in range(80)
        ],
    }
    features = {f: raw[f] for f in candidate.features}
    research_universe = decision_universes(
        pa.table({"frame_time": [FRAME] * 80, "symbol": symbols, **features}), candidate.features
    )
    [(_, universe_symbols, universe_features, _)] = research_universe
    historical = rank_timestamp(candidate, FRAME, universe_symbols, universe_features)
    current = rank_direction(candidate_id, candidate.direction, CANDIDATES, symbols, raw)
    assert current["eligible"] == len(universe_symbols)
    assert [r["symbol"] for r in current["rows"]] == historical.symbols
    assert [r["score"] for r in current["rows"]] == historical.scores
    excluded = {e["symbol"] for e in current["excluded"]}
    assert excluded == set(symbols) - set(universe_symbols)


# -- scientific boundaries ----------------------------------------------------------------------


def test_live_ranking_code_cannot_reach_outcomes_or_research():
    """The live service and the shared core import no research, dataset or
    reservation code, and never name an outcome label."""
    for module in (live, __import__("app.ranking.scoring", fromlist=["x"])):
        source = Path(module.__file__).read_text()
        tree = ast.parse(source)
        imported = {
            n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
        } | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not any(m.startswith("app.research") for m in imported), module.__name__
        assert "forward_" not in source and "read_dataset" not in source
        assert "load_columns" not in source and "evaluations" not in source


def test_the_configured_candidates_are_registered_and_valid():
    settings = get_settings()
    for candidate_id, direction in (
        (settings.live_ranking_long_candidate, "long"),
        (settings.live_ranking_short_candidate, "short"),
    ):
        spec = json.loads((CANDIDATES / f"{candidate_id}.json").read_text())
        assert spec["direction"] == direction
        assert load_candidate_file(CANDIDATES / f"{candidate_id}.json").candidate_id == candidate_id
