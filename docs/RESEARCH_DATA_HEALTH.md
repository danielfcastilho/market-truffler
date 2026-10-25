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

## Scheduled shard verification

The research worker verifies every dataset once per
`RESEARCH_VERIFICATION_INTERVAL_HOURS` (default 24). Each pass checks at
most `RESEARCH_VERIFICATION_MAX_PER_PASS` (default 20), least recently
checked first, in a thread so the heartbeat stays live. It never runs on a
request. It reuses the existing verifiers (`app.research.integrity`):

- **`full`:** `read_dataset(path, columns=["frame_time"])` checks the sha256
  of the whole file against the manifest, the schema and base-timeframe
  metadata, and the row count. Only `frame_time` is loaded, never a label.
- **`content_hash_only`:** for a dataset whose outcome window reaches into
  a reserved prospective period (`reservations.check_access` refuses it),
  only its bytes are hashed against the manifest and the Parquet file is
  never parsed. The result is `restricted`, never `verified`.

Statuses:

- `never_checked`;
- `pending`: the manifest changed since the check, so the old result no
  longer applies;
- `verified`;
- `restricted`;
- `failed`, with the reason: missing file, hash, rows or schema;
- `unavailable`: no readable manifest.

A result older than twice the interval is flagged **overdue**. Results are
operational state (`continuity/verification.json`), written one dataset at
a time, so an interrupted pass loses only the dataset in progress. Nothing
is ever written into a manifest or a dataset, repaired or deleted. **A
verified shard is intact, not scientifically sufficient.**

## Prospective continuity alerts

Each pass, the worker derives alerts from its own facts
(`app.research.research_alerts`):

- its per-day shard decisions, which judge a day only once it is complete
  and its labels have matured;
- registration readiness;
- verification.

One alert per registration partition and condition (days aggregated):

| condition | severity |
| --- | --- |
| `registration_invalid`: the registration does not verify | critical |
| `shard_integrity_failed`: a partition day's shard failed verification | critical |
| `shard_unrecoverable`: a completed partition day has no shard and cannot get one | warning |
| `shard_generation_failing`: a partition day keeps failing to generate | warning |
| `verification_overdue`: a partition day's shard was not re-verified in 2× the interval | warning |
| `awaiting_data`: the partition is ahead (awaiting its window or accumulating) | info |

Immature days, days in progress and future windows never produce warnings.

**Lifecycle** (`continuity/alerts.json`):

- An id is `<registration>/<role>/<condition>`.
- It stays active, keeping its first-observed time, while the condition
  holds, and resolves when it no longer does.
- If it returns, it becomes active again with a new first-observed time and
  an incremented occurrence count.
- Only a completed worker pass updates alerts, so a restart resolves
  nothing.
- A silent worker cannot report itself: the API adds `worker_stale`
  (warning) when the heartbeat is stale, and marks the list as possibly out
  of date.

**Where they show:** actionable alerts appear in Vitals → OINK CORP, as a
"Prospective continuity" row plus the list. On the Data Health page they
appear on the affected registration's card.

## Recent collection continuity

These are rolling 24-hour and 7-day windows on the same 5-minute grid. They
end at the last due boundary, so the forming frame is excluded, and start no
earlier than the first frame ever recorded (`window_shortened`). Each window
reports two separate things:

- **Frame coverage:** finalized frames ÷ expected frames.
- **Analysis completion:** analyzed ÷ finalized frames with members. Frames
  still inside their normal analysis window (decision time + 2 minutes) are
  `pending`, not unanalyzed.

Both are proxies for collection; neither is process uptime, which nothing
records.

## Research backups

Data Health → Research backups reports four separate states, without the
destination path:

- configured;
- last attempt;
- last verified backup, with its age and coverage;
- recovery test, with its scope.

Scope, alerts, the recovery test and the disaster-recovery procedure are
in [OPERATIONS.md](OPERATIONS.md#backups).

## Cost

- Measured on 1,149 frame headers: about 0.3 s.
- Responses are cached for 60 s, and the response carries `computed_at`.
- Manifest reads are small.
- No market API calls, writes, locks or background workers are added.

## Limitations

- Readiness, alerts and dataset verification are only as fresh as the
  worker's last pass (30 minutes); a worker that stops is reported as
  `worker_stale` after 5 minutes.
- Verification reads each dataset file once per interval: about the file
  size in I/O per day, outside live processing.
- Sniffer results older than partition retention show as
  `analysis_unknown`.
- Frame completeness describes collection, not the quality of the features
  computed from it.
