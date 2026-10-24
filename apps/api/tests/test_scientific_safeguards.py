"""Audit remediation, scientific safeguards: structured prediction
direction (F-07), reserved-window generation guard (F-04), ranking
universe/survivorship disclosures (F-06/F-10) — each without changing any
registered artifact's hash."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_campaign import _campaign, _rows

from app.research.campaign import Campaign, CampaignHypothesis, campaign_hash, load_campaign
from app.research.knowledge import hypothesis_sign
from app.research.ranking import RANKING_DISCLOSURES, universe_disclosure
from app.research.reservations import ReservedDataError, check_generation_window
from app.scripts import generate_research_dataset

REPO = Path(__file__).resolve().parents[3]
RESEARCH = REPO / "research/oink_corp"


def _hypothesis(**overrides) -> dict:
    return {"id": "H1", "statement": "s", "prediction": "p", "experiment_id": "x-v1", **overrides}


# -- F-07: structured prediction direction ---------------------------------------------------------


def test_the_registered_campaign_hash_is_unchanged_by_the_new_field():
    campaign, specs = load_campaign(
        RESEARCH / "campaigns/oi-incremental-price-1h-v1.json", RESEARCH / "experiments"
    )
    registration = json.loads(
        (
            RESEARCH / "data/evaluations/oi-price-1h-prospective-v1/registration/registration.json"
        ).read_text()
    )
    assert campaign_hash(campaign, specs) == registration["registration"]["campaign_hash"]
    assert all(h.prediction_direction is None for h in campaign.hypotheses)


def test_a_v1_hypothesis_serializes_exactly_as_before():
    assert json.loads(CampaignHypothesis.model_validate(_hypothesis()).model_dump_json()) == (
        _hypothesis()
    )


@pytest.mark.parametrize(
    ("direction", "text", "sign"),
    [
        ("positive", "A spread, stated freely", "positive"),
        ("negative", "Positive-sounding words do not decide it", "negative"),
        ("none", "anything", "none"),
    ],
)
def test_v2_uses_the_structured_direction_never_the_text(direction, text, sign):
    hypothesis = CampaignHypothesis.model_validate(
        _hypothesis(prediction=text, prediction_direction=direction)
    )
    assert hypothesis_sign(hypothesis) == sign


def test_v1_keeps_the_text_rule():
    assert hypothesis_sign(
        CampaignHypothesis.model_validate(_hypothesis(prediction="Negative x"))
    ) == ("negative")
    assert hypothesis_sign(CampaignHypothesis.model_validate(_hypothesis())) == "unspecified"


def test_v2_requires_a_direction_and_v1_forbids_one(tmp_path):
    path, experiments, _ = _campaign(tmp_path, _rows(2))
    raw = json.loads(path.read_text())
    v2_missing = {**raw, "campaign_version": 2}
    with pytest.raises(ValidationError, match="requires prediction_direction"):
        Campaign.model_validate(v2_missing)
    v1_with = {**raw, "hypotheses": [{**raw["hypotheses"][0], "prediction_direction": "positive"}]}
    with pytest.raises(ValidationError, match="needs campaign_version 2"):
        Campaign.model_validate(v1_with)
    v2 = {**v1_with, "campaign_version": 2}
    assert Campaign.model_validate(v2).hypotheses[0].prediction_direction == "positive"


# -- F-04: ad-hoc generation over reserved days ---------------------------------------------------


def _reserve(data_dir: Path, start: str, end: str) -> None:
    reg = data_dir / "evaluations" / "test-reg-v1" / "registration"
    reg.mkdir(parents=True)
    (reg / "registration.json").write_text(
        json.dumps(
            {
                "registration": {
                    "registration_id": "test-reg-v1",
                    "partitions": [{"role": "validation", "start": start, "end": end}],
                }
            }
        )
    )


def test_generation_whose_outcome_window_reaches_a_reservation_is_refused(tmp_path):
    _reserve(tmp_path, "2026-10-26", "2026-11-25")
    day = datetime(2026, 10, 25, tzinfo=UTC)
    check_generation_window(tmp_path, day - timedelta(days=1), day)  # ends before: fine
    with pytest.raises(ReservedDataError, match="reserved for the validation"):
        # The features end before the reservation, the 24h labels do not.
        check_generation_window(tmp_path, day, day + timedelta(hours=23) + timedelta(hours=24))
    check_generation_window(
        tmp_path, datetime(2026, 11, 25, tzinfo=UTC), datetime(2026, 11, 26, tzinfo=UTC)
    )


def test_the_generator_cli_refuses_before_touching_the_database(tmp_path, monkeypatch, capsys):
    _reserve(tmp_path, "2026-10-26", "2026-11-25")
    monkeypatch.setattr(
        generate_research_dataset,
        "get_settings",
        lambda: type("S", (), {"research_data_dir": str(tmp_path)})(),
    )

    def no_db(*args, **kwargs):
        raise AssertionError("the database must not be touched")

    monkeypatch.setattr(generate_research_dataset, "get_engine", no_db)
    monkeypatch.setattr(generate_research_dataset, "get_session_factory", no_db)
    monkeypatch.setattr(
        "sys.argv",
        [
            "x",
            "--start",
            "2026-10-27T00:00:00Z",
            "--end",
            "2026-10-28T00:00:00Z",
            "--output",
            "peek",
        ],
    )
    with pytest.raises(SystemExit) as exit_info:
        generate_research_dataset.main()
    assert exit_info.value.code == 1
    assert "reserved" in capsys.readouterr().err
    assert not (tmp_path / "peek").exists()


# -- F-06 / F-10: ranking disclosures -------------------------------------------------------------


class _Model:
    def __init__(self, *features: str) -> None:
        self.features = list(features)


def test_the_universe_disclosure_names_features_only_the_reference_requires():
    same = universe_disclosure(_Model("return_1h", "oi_change_1h"), _Model("return_1h"))
    assert "matches the candidate's live ranking universe" in same
    narrower = universe_disclosure(_Model("return_1h"), _Model("return_1h", "oi_change_1h"))
    assert "narrower" in narrower and "oi_change_1h" in narrower
    assert "matches" in universe_disclosure(_Model("return_1h"), None)


def test_ranking_reports_disclose_survivorship():
    text = " ".join(RANKING_DISCLOSURES)
    assert "survivorship" in text and "top_k_missing_outcome" in text
