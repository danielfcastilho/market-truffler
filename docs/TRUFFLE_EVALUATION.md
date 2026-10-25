# Prospective Truffle evaluation and model knowledge

Implemented in:

- `apps/api/app/research/truffle_evaluation.py`, with its CLI
  `app.scripts.truffle_evaluation`;
- `apps/api/app/research/model_knowledge.py`, with its CLI
  `app.scripts.model_knowledge`.

Registered on 2026-10-25T01:48:57Z as **`truffle-reversal-oi-prospective-v1`**
(registration hash `8d781bb8…`).

## What it measures, and what it does not

It measures whether coins qualified as experimental Truffles show a
different subsequent **market-relative return in their direction** than
the eligible universe of the same timestamps.

It does not measure:

- profitability;
- an achievable entry return, because labels start at the frame's
  candle close T while live Truffles appear about T+110s;
- costs, slippage, funding or execution;
- whether the ranking itself is good.

It is observational.

## Hypotheses (frozen)

Each hypothesis is a separate claim. They are never pooled into one
"Truffles work" claim.

| id | policy (hash) | ranking model (hash) | utility | predicted |
| --- | --- | --- | --- | --- |
| T-LONG | `long-reversal-oi-truffle-v1` (`eb39d85d…`) | `long-price-reversal-oi-v1` (`2841de36…`) | +`forward_relative_return_1h` | higher than reference (structured `prediction_direction: positive`) |
| T-SHORT | `short-reversal-oi-truffle-v1` (`5cc6169a…`) | `short-price-reversal-oi-v1` (`e5161ee5…`) | −`forward_relative_return_1h` | higher than reference |

`forward_relative_return_H` = the forward return over H minus the
cross-sectional median of the same frame (the existing label). The sign
convention matches Evaluator v1 and ranking research: Long +label,
Short −label.

## Universe, selection, reference

- **Data:** the continuity worker's daily shards (`shard_s15m_*`, one
  frame every 15 minutes) of each partition day, as preserved. A day
  without a shard, or whose shard was not produced under temporal contract
  v2, is recorded as missing and never filled.
- **Universe:** per frame, the members whose every ranking-model feature
  is finite, which is the live ranking's eligible universe. Frames with
  fewer than 50 such coins are not evaluated.
- **Selection:** the live rule exactly: `app.ranking.qualification`
  (evaluator v1) over that universe. A test checks that evaluated and live
  Truffles are identical for the same frame.
- **Primary reference:** the same-timestamp eligible universe. It
  **includes the Truffles**, so it is not an independent control.
- **Secondary analyses** (no verdict, and they never change a decision):
  - the same comparison at `forward_relative_return_4h`;
  - Truffles versus eligible **non-Truffles** at 1h;
  - qualification frequency and coverage.
- **Unit of observation:** the timestamp. Per frame, the spread is the
  mean Truffle utility minus the reference mean. Each timestamp weighs
  equally regardless of its Truffle count. Coins are never independent
  observations.
- **Zero-Truffle frames:** valid. They count in coverage
  (`timestamps_without_selection`), contribute no spread, and no
  qualified-group return is fabricated for them.
- **Missing outcomes:** a Truffle without a label is excluded after
  selection and counted (`selected_missing_outcome`). This survivorship
  risk is disclosed, not corrected.

## Statistics and decisions

Evaluator v1 (`app.research.evaluation.evaluate`, validation segment) is
reused unchanged:

- the timestamp-weighted mean spread;
- a 90% day-block bootstrap: 2000 resamples, whole days resampled
  together, which covers overlapping 1h/4h windows from 15-minute
  sampling; `effective_independent_windows` is reported;
- its frozen minimum evidence: at least 10 qualifying days, 200
  timestamps with Truffles and 30 distinct Truffle coins, at most a 20%
  top-coin share, and at least 60% of days consistent;
- seeds derived deterministically from the registration hash.

**Multiplicity.** The primary family is {T-LONG, T-SHORT}, corrected with
Bonferroni: the same day-block interval is also computed at 95%,
1 − 0.10/2.

| decision | rule |
| --- | --- |
| `insufficient_evidence` | Evaluator v1 `insufficient_data` |
| `consistent_with_improvement` | Evaluator v1 `positive_evidence` **and** adjusted 95% lower bound > 0 |
| `inconsistent_with_improvement` | Evaluator v1 `negative_evidence` **and** adjusted 95% upper bound < 0 |
| `inconclusive` | anything else, including a directional verdict that does not survive the adjustment |

A decision is about relative returns in this sample, never about
profitability.

## Periods and lifecycle

- **Validation:** 2026-12-27 → 2027-01-26 (exclusive). Eligible from
  2027-01-26 once every day has a shard, otherwise 2027-01-28.
- **Protected holdout:** 2027-01-27 → 2027-02-26. Eligible from
  2027-02-26, otherwise 2027-02-28. Evaluated only with
  `--protected-holdout`, at most once, and never if any registration
  exposed its period.

Lifecycle per partition: `registered_awaiting_data` →
`eligible_for_evaluation` → `evaluated`, with each claim's decision
(`insufficient_evidence` is one decision). Evaluation is refused before
eligibility.

Every attempt appends events: `authorized` → `started` (written before any
label is read) → `completed` / `failed`. The flow is shared with
`app.research.prospective`.

## How protected periods stay isolated

The existing registration `oi-price-1h-prospective-v1` reserves
2026-10-26 → 2026-12-26 (validation, then holdout). Its files are
unchanged and it still verifies. Isolation works as follows:

- Access grants are per registration (`app.research.reservations`). A
  Truffle window overlapping those days could only be evaluated by reading
  another study's reserved labels. So Truffle registrations refuse any
  window within the embargo (1 day) of **any** existing partition
  (`reservation_conflicts`). Its first possible day was 2026-12-27.
- Once registered, the Truffle partitions are reservations like any
  other. `load_columns` (the only research path to labels) and the dataset
  generator refuse them without that evaluation's live grant.
- Campaign-claim loaders (`prospective.load_all`) skip other registration
  kinds. A campaign holdout check also sees periods exposed by Truffle
  registrations (`other_kind_exposures`).

## Freezing

The registration binds:

- exact ranking-model and qualification-spec hashes;
- the qualification evaluator version;
- Evaluator v1's version and minimum-evidence constants;
- the temporal contract version;
- the 50-coin cross-section minimum.

`--verify` fails, and evaluation is refused, if any of them changed. A
changed spec never inherits a registration: it needs a new id and a new
registration. The live specs and thresholds were not changed.

## Model knowledge

**Identities.** Each ranking model (`ranking_model`) and each
qualification policy (`qualification_policy`) is its own identity:

- id and spec hash;
- family or policy kind, direction, features and transforms;
- parameter summary;
- source file.

A policy names the model it applies to. A model does not require a
policy.

**Ledgers.** One per identity, at
`research/oink_corp/knowledge/models/{ranking,qualification}/<id>/NNNN.json`.
They are written with the campaign ledger's own revision mechanism
(`knowledge.append_revision`): numbered, hash-chained, never overwritten,
and appended only when the derived content changes. `--sync` is
idempotent. A changed spec hash under an existing id is refused.

**Evidence classes** (never interchangeable):

| class | meaning |
| --- | --- |
| `human_hypothesis` | the spec's own stated objective; not evidence |
| `exploratory_development` | ranking-study runs on development data (flagged when their datasets predate temporal contract v2) |
| `prospective_registration` | a registration binding this hash |
| `prospective_validation` / `protected_holdout` | independent completed evaluations, verified against their exposure log |
| `prospective_reused` | a completed evaluation that was not independent |
| `operational_observation` | reserved; none are recorded |

**Statuses**, the weakest the evidence justifies:

- `experimental`
- `registered_for_prospective_evaluation`
- `insufficient_prospective_evidence`
- `inconclusive_prospective_evidence`
- `supported_by_prospective_evidence`
- `contradicted_by_prospective_evidence`
- `conflicting_prospective_evidence`

Rules for moving between them:

- Only independent prospective decisions move a status. Live use, Truffle
  counts, development results and the number of experiments never do.
- A Truffle evaluation moves the **policy's** status only. The ranking
  model links to it but stays registered, because nothing has evaluated
  the ranking itself.
- `--verify` re-derives the status from exactly the evidence a revision
  cites and rejects anything else, even with a matching content hash.
- Nothing is promoted automatically.

**Current state.** All four identities are
`registered_for_prospective_evaluation`. Both ranking models link one
exploratory study run on temporal-contract-v1 data (`insufficient_data`).

**The Research Explorer.** OINK CORP → Model knowledge
(`GET /api/research/models`) shows:

- the latest revision of each identity, with its hash chain re-checked;
- the Truffle registration with its lifecycle;
- every limitation.

## Why this is not yet a learning system

The ledgers record what was registered and observed. They do not propose,
fit, compare or select models.

Before Controlled Model Search, at minimum:

- this registration's validation must complete and be judged by its
  frozen rules;
- the multiple-testing requirements in `ARCHITECTURE.md` (family counting
  across every searched variant) must be implemented;
- a search budget and a reserved, never-exposed confirmation period must
  be fixed in advance;
- each searched model needs its own identity and ledger.
