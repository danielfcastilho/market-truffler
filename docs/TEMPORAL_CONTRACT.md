# Temporal contract

When may an input be used for a Market Frame? This document is the single
answer, implemented in `apps/api/app/domain/temporal.py` and applied
identically by live Sniffer analysis, startup recovery, historical replay
and research dataset generation. Version: **2** (recorded as
`temporal_contract_version` in dataset manifests from this version on;
manifests without the field were produced under version 1).

## Five instants

| Instant | Meaning | Where it lives |
| --- | --- | --- |
| Observation time | When the measured fact holds | OI `observed_at` (5-minute bucket), funding `funding_time` (settlement), candle `open_time`/`close_time` |
| Availability time | When the fact could first be known *here* | not observable directly; evidenced by ingestion time |
| Ingestion time | When MARKET first stored this value | `ingested_at` on OI and funding rows (NULL before contract v2) |
| Frame time `T` | The 5-minute boundary a frame describes | `market_frames.frame_time` |
| Decision time `D(T)` | When frame `T`'s inputs are frozen for measurement | `T + 60s` (`DECISION_DELAY`) |

MARKET never learns the exchange's publication instant, so ingestion time
is the availability evidence. It can only be *later* than the true
availability, never earlier, so filtering on it can drop a usable value but
cannot admit a future one.

## The rule

An Open Interest or funding observation is usable for frame `T` only if

    observed_at <= T   AND   (ingested_at <= D(T)  OR  ingested_at IS NULL)

`ingested_at IS NULL` marks a row stored before ingestion was recorded
(availability unknown). Such rows are accepted, so history from before
contract v2 replays exactly as it did, and every report built on them
discloses that their availability is unverified. NULL is never backfilled
or guessed: no historical ingestion time is ever manufactured.

Candles are governed by frame membership: a frame references only candles
closed by `T` (`open_time <= T - duration`), selected when the frame
finalized. That rule is unchanged.

Consequences, by feature:

- `oi_change_*`: "current OI" is the latest bucket observed at or before
  `T` *and* available by `D(T)`; "previous OI" is the bucket exactly one
  lookback earlier, under the same bound. A bucket stored after `D(T)` is
  skipped and never waited for: the anchor falls back to the latest bucket
  that was available. Live and replay make the same choice.
- `funding_rate_current` / `funding_rate_24h_avg`: settlements with
  `funding_time <= T` that were also stored by `D(T)`. A settlement exactly
  at `T` counts only if MARKET had it by `D(T)`.
- Everything is measured *at* `D(T)`: if Sniffer's hook fires earlier
  (frames finalize about `T+30s`), analysis waits. A later analysis
  (recovery, replay, dataset generation) applies the same cutoff, so when
  it runs does not change what it measures.

## Why 60 seconds

Measured on 2026-10-24 against Bybit's public REST API:

- OI bucket `T` appears 10-20s after `T` (all 808 linear USDT symbols by
  `T+20s` across several boundaries) and was never revised through
  `T+240s`.
- A full-universe OI round at 25 concurrent requests takes about 15s, so a
  round starting at `T+15s` finishes about `T+30s`; the few symbols
  published after the round reached them are retried once about 5s later.
- Funding settlements: see "Funding publication" below.

Both reconcilers therefore run one round per boundary, a fixed offset after
it (OI `+15s`, funding `+2s`), instead of an unaligned 300-second loop.
Under the old loop, at analysis time about half the universe's newest
stored bucket was `T-5m` or `T-10m`, while a later replay used bucket `T`
for every coin. The audit (F-01) measured about 79% of `oi_change_*` values
disagreeing between live and replay for that reason.

`DECISION_DELAY` is part of the research contract, not configuration:
changing it changes replayed values. It is a constant, versioned with this
document.

## Funding publication

Measured at the 23:00 UTC settlement on 2026-10-24 (six 1h-interval
symbols, polled from T-30s to T+600s): every settlement was listed by
`/v5/market/funding/history` at T+0s and not revised afterwards. The first
deployment of the aligned funding round (start T+20s, 15 concurrent
requests) stored the last of them at T+64s, after the decision time. The
temporal filter then correctly kept the previous settlement for those
coins, and replay agrees. The round now starts at T+2s with 25 concurrent
requests (~20s for the universe). Settlements at hours that are 4h/8h
boundaries involve every symbol, so they are the case this timing exists
for.

## Provenance rules for `ingested_at`

- Set by the reconcilers (and the OI gap-repair script) to the instant the
  response was stored.
- First-seen: re-fetching the same value keeps the original instant, a
  legacy NULL included.
- An exchange-side revision of the value records when the *new* value
  became available. The previous value is not kept, so frames measured
  before the revision lose that input in replay rather than see a value
  they could not have had. No revisions were observed in testing.
- Bootstrap and gap repair store historical buckets with their real,
  late ingestion instant, so they never count for frames measured before
  they were stored.

## What this does not cover

- Frames, sniffer results and datasets from before contract v2 are not
  rewritten. Their OI/funding rows have NULL `ingested_at`, so a replay of
  those frames uses whatever was eventually stored. That reproduces the
  research datasets already generated, not the values live Sniffer
  measured then. The live/replay disagreement documented by the audit
  remains a property of that historical period.
- Candle corrections that land after a frame finalized are still visible
  to a replay of that frame (the frame stores candle references, not
  values).
- Availability relies on the local clock (the same trust model as
  `registered_at` in prospective registrations).

## Checking parity

`python -m app.scripts.parity_diagnostic --frames 12` (inside the api
container, read-only) replays the most recent analyzed frames and reports
per-feature mismatch rates against the stored live results. For frames
analyzed under contract v2 it should report no OI or funding mismatches.
Candle corrections can still show up as rare return/volatility mismatches.
