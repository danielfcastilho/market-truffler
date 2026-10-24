"""OINK CORP research campaigns: a pre-registered group of experiments that
answer one research question together.

A campaign is a committed JSON file (`research/oink_corp/campaigns/`) —
the question, the hypotheses and the predictions made *before* any outcome
was examined, which experiment specs test them, which simpler specs are
their baselines, and which candidate/reference pairs are the primary
incremental comparisons (`app.research.comparison`). It holds no results:
those are the immutable run records each execution writes, and the
interpretation lives in the campaign's report next to it.

Like an experiment spec, a campaign is pure data and cannot influence
evaluation. Its hash (over the campaign file and every spec it names) is
written into each comparison run, so a result can always be traced to the
exact pre-registration it answers.
"""

import json
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    Field,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    ValidationError,
    model_serializer,
    model_validator,
)

from app.research.comparison import check_nested
from app.research.experiment import (
    ExperimentSpec,
    ExperimentSpecError,
    _canonical_hash,
    _Frozen,
    load_spec,
    spec_hash,
)

#: Campaign v2 adds `CampaignHypothesis.prediction_direction`, the
#: structured sign of the prediction (audit F-07); v1 campaigns keep the
#: sign parsed from the prediction's text (`app.research.knowledge`).
CAMPAIGN_VERSION = 2
_SLUG = r"^[a-z0-9][a-z0-9-]{0,79}$"


class CampaignHypothesis(_Frozen):
    id: str = Field(pattern=r"^H[0-9]{1,2}$")
    statement: str = Field(min_length=1, max_length=2000)
    #: The pre-registered expectation, in words — including "no direction
    #: assumed" where that is the honest prior.
    prediction: str = Field(min_length=1, max_length=2000)
    experiment_id: str = Field(pattern=_SLUG)
    #: Campaign v2: the registered sign of the prediction — "positive" /
    #: "negative" spread, or "none" (no direction assumed). Required in v2,
    #: absent in v1.
    prediction_direction: Literal["positive", "negative", "none"] | None = None

    @model_serializer(mode="wrap")
    def _omit_unset_direction(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> Any:
        # A v1 hypothesis serializes exactly as before the field existed, so
        # registered campaign hashes and ledger snapshots are unchanged.
        data = handler(self)
        if isinstance(data, dict) and data.get("prediction_direction") is None:
            data.pop("prediction_direction", None)
        return data


class Baseline(_Frozen):
    id: str = Field(pattern=_SLUG)
    description: str = Field(min_length=1, max_length=2000)
    #: None for the Evaluator's built-in same-timestamp eligible universe.
    experiment_id: str | None = Field(default=None, pattern=_SLUG)


class Comparison(_Frozen):
    comparison_id: str = Field(pattern=_SLUG)
    hypothesis: str = Field(pattern=r"^H[0-9]{1,2}$")
    candidate: str = Field(pattern=_SLUG)
    reference: str = Field(pattern=_SLUG)


class Campaign(_Frozen):
    campaign_version: Literal[1, 2]
    campaign_id: str = Field(pattern=_SLUG)
    created: date
    question: str = Field(min_length=1, max_length=4000)
    motivation: str = Field(min_length=1, max_length=4000)
    hypotheses: list[CampaignHypothesis] = Field(min_length=1, max_length=10)
    baselines: list[Baseline] = Field(min_length=1, max_length=10)
    comparisons: list[Comparison] = Field(min_length=1, max_length=10)
    parameters_rationale: str = Field(min_length=1, max_length=8000)
    evaluation_plan: str = Field(min_length=1, max_length=8000)

    @model_validator(mode="after")
    def _check_references(self) -> "Campaign":
        for h in self.hypotheses:
            if self.campaign_version == 1 and h.prediction_direction is not None:
                raise ValueError(f"{h.id}: prediction_direction needs campaign_version 2")
            if self.campaign_version >= 2 and h.prediction_direction is None:
                raise ValueError(f"{h.id}: campaign_version 2 requires prediction_direction")
        hypothesis_ids = [h.id for h in self.hypotheses]
        if len(hypothesis_ids) != len(set(hypothesis_ids)):
            raise ValueError("hypothesis ids must be unique")
        comparison_ids = [c.comparison_id for c in self.comparisons]
        experiments = set(self.experiment_ids)
        if len(comparison_ids) != len(set(comparison_ids)) or experiments & set(comparison_ids):
            raise ValueError("comparison ids must be unique and differ from experiment ids")
        for comparison in self.comparisons:
            if comparison.hypothesis not in hypothesis_ids:
                raise ValueError(f"{comparison.comparison_id}: unknown hypothesis")
            if {comparison.candidate, comparison.reference} - experiments:
                raise ValueError(f"{comparison.comparison_id}: references unlisted experiments")
        return self

    @property
    def experiment_ids(self) -> list[str]:
        """Every experiment the campaign runs, hypotheses first."""
        ids = [h.experiment_id for h in self.hypotheses] + [
            b.experiment_id for b in self.baselines if b.experiment_id is not None
        ]
        return list(dict.fromkeys(ids))


def load_campaign(path: Path, experiments_dir: Path) -> tuple[Campaign, dict[str, ExperimentSpec]]:
    """Load and validate a campaign and every spec it names: each spec's id
    matches its file, all share datasets and label (one comparable sample),
    and every comparison is a valid nested pair."""
    try:
        campaign = Campaign.model_validate_json(path.read_text())
    except ValidationError as exc:
        raise ExperimentSpecError(f"invalid campaign {path}: {exc}") from exc
    specs: dict[str, ExperimentSpec] = {}
    for experiment_id in campaign.experiment_ids:
        spec = load_spec(experiments_dir / f"{experiment_id}.json")
        if spec.experiment_id != experiment_id:
            raise ExperimentSpecError(f"{experiment_id}.json declares {spec.experiment_id!r}")
        specs[experiment_id] = spec
    first = next(iter(specs.values()))
    for spec in specs.values():
        if spec.datasets != first.datasets or spec.label != first.label:
            raise ExperimentSpecError(
                f"{spec.experiment_id}: every campaign experiment must use the same "
                "datasets and label"
            )
    for comparison in campaign.comparisons:
        check_nested(specs[comparison.candidate], specs[comparison.reference])
    return campaign, specs


def campaign_hash(campaign: Campaign, specs: dict[str, ExperimentSpec]) -> str:
    return _canonical_hash(
        {
            "campaign": json.loads(campaign.model_dump_json()),
            "spec_hashes": {k: spec_hash(v) for k, v in sorted(specs.items())},
        }
    )
