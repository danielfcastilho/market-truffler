# Research data health

OINK CORP → **Data health** (`GET /api/research/data-health`). It is a
read-only, descriptive view of the data future research depends on.
**Complete data is a precondition for research, not evidence of anything.**
Vitals stays responsible for live operational health; this view only
reports research readiness.

Implementation:

- `apps/api/app/services/research_data_health.py`, for the API side;
- `apps/api/app/research/readiness.py`, run by the research worker.

## Frame coverage

Source: `market_frames` headers (never pruned), plus one index probe per
frame into `sniffer_results` for analysis provenance.

- **Observation interval.** From the first frame ever recorded on the
  canonical 5-minute grid to the last boundary whose frame should have
  finalized (boundary + 2 minutes). Nothing before the first frame is
  assumed, and the frame being built is never counted as missing.
- **Each grid slot is exactly one of:**
  - `missing`: no frame row;
  - `building`: a row that never finalized, i.e. incomplete;
  - `partial`: finalized with fewer members than expected;
  - `complete`.

  Coverage = finalized slots ÷ expected slots.
- **Finalized frames with members are also one of:**
  - `analyzed`: they have Sniffer results;
  - `unanalyzed`: no results, within the results retention;
  - `analysis_unknown`: older than the oldest retained results, so pruned
    rather than missing.
- **Off-grid frames** (from before 5m became the base timeframe) are
  counted separately and never as coverage.
- **Daily (UTC):** a full day has 288 slots. The first and last observed
  days are flagged `partial_day` when the interval cuts them short. Only
  their in-interval slots are expected, so a partial boundary day is not a
  failed collection day.
- **Intervals:** covered and missing runs, with the 50 most recent of each
  shown and the total count always given.

## Research shards

- **Inventory.** Every dataset directory under the research data directory,
  from its `manifest.json` only. **No Parquet file is opened**, so no label
  (reserved or not) is read. Each entry reports:
  - whether the file is present;
  - the manifest's own sha256;
  - recorded fields, with absent ones reported as such, never filled;
  - structural problems: missing required fields, a `shard_day` or frame
    times inconsistent with a daily shard's name.
- **Verification.** It is the research worker's last *explicit*
  re-verification (`verified` / `failed`). Otherwise it is `not_checked`:
  a manifest that parses is never called verified.
- **Continuity.** The worker's own per-day decisions since `shards_start`:
  - `preserved`;
  - `collection_gap` or `source_expired` (unrecoverable);
  - `waiting_maturity` or `waiting_readiness`;
  - `generate`.

  Overlapping or duplicate dataset intervals are flagged.
- **Status levels, which mean different things:** the file exists → the
  manifest is valid → continuity is satisfied → evaluation is ready (below).

## Temporal contract

- **Frames:** analyzed frames are grouped by Sniffer analysis provenance.
  `v2` results carry it; `v1` results predate it, so their OI/funding
  availability is unverified.
- **Datasets:** grouped by the manifest's `temporal_contract_version`. A
  manifest without one is `unrecorded`, meaning it was produced before v2.
  Nothing old is re-labelled.

## Prospective readiness

The research worker computes it every pass (`app.research.readiness`) with
the protocol's own functions:

- registration verification;
- partition states and eligibility times.

The API only displays that report and its timestamp, so it can never
disagree with what an evaluation would accept.

Phases per partition:

- `awaiting_window`;
- `accumulating`;
- `awaiting_eligibility`: the window ended but the eligibility time has
  not come;
- `eligible`: the holdout additionally needs `--protected-holdout`;
- after an attempt: `evaluated` / `failed` / `exposure_uncertain`.

Each partition also lists its elapsed days without a shard and whatever
still blocks evaluation. A registration that fails verification is
reported and never repaired. Readiness writes no exposure event and opens
no dataset.

## Cost

- Measured on 1,149 frame headers: about 0.3 s.
- Responses are cached for 60 s, and the response carries `computed_at`.
- Manifest reads are small.
- No market API calls, writes, locks or background workers are added.

## Limitations

- Readiness and dataset verification are only as fresh as the worker's
  last pass (30 minutes).
- Sniffer results older than partition retention show as
  `analysis_unknown`.
- Frame completeness describes collection, not the quality of the features
  computed from it.
