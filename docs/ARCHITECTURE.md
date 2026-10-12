# Architecture

This document describes two things: the **future conceptual system** Market
Truffler is being built toward, and the **actual state** of the current
milestone. Don't confuse the two — much of what's below is not implemented
yet, by design. Sections describing future design say so explicitly.

## The future system

> **Sniffer senses. OINK CORP thinks. Warhog acts.**

Three product areas form the trading pipeline, plus one operational area
that observes all of them. These are *logical* product boundaries — modules
inside the existing `apps/api` and `apps/web` applications — not separate
services, databases, containers, or deployment units.

```
                         BYBIT
                           │
                           ▼
        🐽 SNIFFER — SENSES  (implemented)
  market data, history, integrity, completeness,
  Market Frames, features ("Sniffs")
                           │  point-in-time observations
              ┌────────────┴─────────────┐
              ▼                          ▼
  🧬 OINK CORP — RESEARCH       🧬 OINK CORP — RANKING
  (offline; partially            (live, deterministic;
   implemented)                   not implemented)
  heuristics → hypotheses        approved Champion model
  → experiments → evaluation     → Long / Short Scores
  → Challenger models            → Rank → Qualification
              │                          ▲      │
              └── human-approved ────────┘      ▼
                  promotion                🍄 Truffles
                                                │
                                                ▼
                                 🐗 WARHOG — ACTS (not implemented)
                       entries, sizing, Martingale-style position
                       management, hedging, exits, orders, fills

          🩺 VITALS — observes the health of all of the above
```

### Domain responsibilities

**🐽 Sniffer — senses. "What is happening in the market?"** Bybit
connectivity, market-data acquisition (the canonical 5m candles), historical
storage, integrity and completeness, Market Frames, feature computation
(Sniffs), historical feature reconstruction (the same `FeatureEngine`,
reused by OINK CORP's replay), and Market Data inspection in the UI.
("MARKET", used in section names below and in Vitals, is Sniffer's
market-data layer.) Sniffer produces observations and measurements only. It does not decide
whether a coin is a desirable Long or Short, and it does not own research,
model evaluation, Champion/Challenger management, or rankings.

**🧬 OINK CORP — thinks.** Everything between observations and trading
opportunities, in two operationally isolated halves:

- **Research** (offline): investigates which market characteristics,
  conditions and relationships identify attractive Long/Short
  opportunities — from human heuristics and, eventually, from its own
  hypotheses — under one shared evaluation standard. Produces Challenger
  models. Partially implemented: historical replay + labeled research
  datasets (see "OINK CORP: historical Sniff replay and research
  datasets").
- **Ranking** (live): consumes current Sniffer observations and evaluates
  them with the explicitly approved Champion model, producing Long/Short
  Scores, Ranks and qualified opportunities (🍄 Truffles). Deterministic;
  never depends on an LLM; never runs experiments; never incorporates an
  unvalidated hypothesis. Not implemented — no model has been approved.

A failed or running experiment must never affect live ranking. See "OINK
CORP: future design" below.

**🐗 Warhog — acts.** Entries, position sizing, Martingale-style
progression, active-position management, recovery, emergency behavior,
hedging, exits, orders, fills, reconciliation. Consumes Truffles. Not
implemented.

**🩺 Vitals — observes system health.** Not part of the trading pipeline.
Answers "is Market Truffler operating correctly?" Monitors real runtime
components — and shows an honest "N/A", never a simulated value, for areas
that don't exist yet. Partially implemented (see below).

Today: Sniffer is implemented end to end (23 Sniffs per instrument per
Market Frame — see "Sniffer measures" below); OINK CORP has only its
offline research foundation; Warhog does not exist. No ranking model,
Score, Rank, Qualification or Truffle exists anywhere, and no Martingale
parameters or execution architecture have been decided.

### Bybit connectivity and the market universe

A read-only connection to Bybit's public REST API answers "can we reach
Bybit?" and "how many instruments are in the universe we care about?"

```
app/integrations/bybit/client.py   HTTP boundary: base URL, timeouts, the
                                    retCode envelope. The only place that
                                    knows Bybit's REST wire format.
        │
        ▼
app/services/market_universe.py   Paginates instruments-info, filters to
                                   linear/USDT-settled/perpetual/tradable,
                                   and maps to app/domain/market.py's
                                   Instrument — the app's own model, not
                                   Bybit's raw response shape.
        │
        ▼
app/routers/market.py (`GET /api/market/status`)   Presentation boundary
                                   consumed by Vitals. Converts a Bybit
                                   failure into a truthful "down" response
                                   rather than letting the app crash or the
                                   caller see a 500.
```

### Canonical base timeframe: 5m

**5m is the single canonical base candle timeframe. 1m is unsupported** —
it is never requested from Bybit, never stored by new code, and nothing
reads it. Every timeframe and duration lives in one place,
`app/domain/timeframes.py` (`BASE_TIMEFRAME`, `BASE_DURATION`,
`BYBIT_BASE_INTERVAL`, `TIMEFRAMES`, and the epoch-aligned
`floor_to_boundary`/`is_aligned`/`is_base_multiple` helpers); nothing else
hardcodes 300 seconds.

| Timeframe | Source | Built from |
|---|---|---|
| 5m | Bybit — WS `kline.5.{symbol}` live, REST `/v5/market/kline?interval=5` history | — |
| 15m / 1h / 4h / 24h | Derived locally | 3 / 12 / 48 / 288 stored 5m candles |

**Candle timestamp semantics.** A candle is identified by its `open_time`
and covers `[open_time, open_time + duration)`. It becomes knowable
("closed") at instant `open_time + duration`; `close_time` is stored just
before that (1ms before for Bybit candles, 1µs for derived ones). So the
5m candle opened at 10:00 UTC is usable by a decision at 10:05 or later,
never by one before 10:05. Every boundary is UTC-epoch-aligned.

**Never an unfinished candle.** Live: only `confirm=true` WebSocket pushes
are ingested. REST: Bybit returns every candle *overlapping* the requested
`[start, end]` (verified live), including the one still forming, so the
reconciler requests `end = boundary - 1ms`, keeps catch-up chunks on 5m
boundaries, and additionally drops any row whose `open_time + 5m` is still
in the future before ingesting.

**Decision cadence.** Market Frames — and therefore Sniffs — are produced
once per closed UTC **5-minute** boundary, the only instants at which a
base candle has just closed (see "MARKET synchronizes" below). At
`frame_time = T`, `member.m5` is the candle `[T-5m, T)`, and its close is
"the price knowable at T".

**Feature availability.** Every feature keeps its meaning, because every
lookback is defined in *elapsed time*:

- `return_{5m,15m,1h,4h,24h}`: `m5.close` vs. the 5m candle exactly
  `lookback` earlier (`m5.open_time - lookback`), so `return_1h` is still
  60 minutes (12 candles back), never 60 candles. Lookbacks must be whole
  multiples of 5m (enforced when the feature is constructed).
- `rsi_14_*`, `volatility_*`, `relative_volume_*`: defined in *candle
  count* (15 consecutive candles) on their own timeframe's candles, which
  were already 5m/15m/1h/4h/24h — unchanged. `rsi_14_5m` now reads native
  Bybit 5m candles instead of 1m-derived ones.
- `oi_change_*`, `funding_rate_*`: read their own observation tables
  against `frame_time` — unchanged (Open Interest was always 5-minute
  buckets).

**Database migration (`a9c4e2f7b3d1`, applied automatically, non-destructive).**
`market_frame_members.open_time_1m` becomes nullable and is no longer
mapped. Every instrument's `history_synced_from` is reset to its
(5m-floored) `history_synced_through`, so the history reconciler re-walks
the whole retention window at 5m from Bybit: the 5m rows already stored
were derived from 1m, and the app must not depend on 1m-derived data.
Re-fetched candles are idempotent upserts, so there is no gap while that
runs; "Historical coverage" and symbol readiness on Vitals dip and recover
over a few minutes. Legacy frames persisted at non-5m minutes stay in
`market_frames` but are never replayed or sampled.

Existing `timeframe = '1m'` candle rows are left in place, unread, and age
out with normal monthly partition retention (the retention guard no longer
counts `open_time_1m` references). **Optional manual cleanup** — not run
automatically because it deletes data; run only once you no longer want
the legacy rows (e.g. to shrink the partitions sooner, which also speeds up
the frame read below):

```sql
-- inside: docker compose exec postgres psql -U truffler -d market_truffler
DELETE FROM market_candles WHERE timeframe = '1m';   -- tens of millions of rows; slow
VACUUM (ANALYZE) market_candles;
ALTER TABLE market_frame_members DROP COLUMN open_time_1m;
```

**Frame read performance.** `FrameRepository.get_frame` (used by Sniffer
and by research replay) measured ~19s per frame against this project's
local data (~61M 1m + ~12M 5m candle rows) with its 1m join; after removing
that join it measured ~3.7s. The remainder is pre-existing: the planner
hash-joins one timeframe's unbounded subquery via a sequential scan of
every candle partition — still physically including the retired 1m rows
until they age out or are deleted. Not addressed in this migration.

### Live MARKET watching: the WebSocket collector

Beyond REST discovery, MARKET now continuously watches the discovered
universe over Bybit's public WebSocket and turns confirmed klines into
canonical closed 5-minute candles:

```
app/integrations/bybit/ws_client.py   BybitKlineWebSocketClient: one WS
                                       connection's full lifecycle — connect,
                                       batched subscribe, 20s heartbeat
                                       ping, reconnect-with-backoff. The
                                       only place that knows Bybit's WS wire
                                       format (kline.{interval}.{symbol}
                                       topics, the `confirm` field). Scoped
                                       to the kline topic today; other public
                                       topics can be added alongside it.
        │
        ▼
app/services/market_collector.py   MarketCollector: a long-lived,
                                    continuously-running capability, started
                                    once from the FastAPI lifespan — not by
                                    any request. Uses the existing
                                    MarketUniverseService (same universe
                                    definition, no second copy), shards its
                                    symbols across several WS connections
                                    (200 symbols/connection, so one
                                    connection's reconnect only blacks out a
                                    fraction of the universe), and drops any
                                    kline where `confirm=False` — only
                                    closed candles cross into
                                    app/domain/market.py's ClosedCandle.
        │
        ▼
app/services/live_candle_sink.py (PersistingCandleSink)
   — resolves symbol → instrument_id and funnels each live candle through
     the same CandleIngestionService the REST history reconciler uses (see
     below), so live and historical delivery share one persistence path.
        │
        ▼
Vitals ("Market data" / "Last market update" / "Data freshness" /
"Historical coverage") — a read-only snapshot of collector + reconciler
   state. Vitals observes them; it never starts, stops, or otherwise
   drives either.
```

Bybit is treated as an external dependency throughout: unreachable Bybit
(REST or WebSocket) never fails `/health` or `/ready`, and never stops the
API from starting or running. Every MARKET row in Vitals is real:
"Bybit connectivity"/"Symbols tracked" come from a REST call made on each
request; "Market data"/"Last market update"/"Data freshness" come from the
background collector's live state; "Historical coverage" comes from the
history reconciler's persisted state (see below).

Candle identity is `(exchange/instrument, timeframe, open_time)` — the
database's composite primary key — so a duplicate delivery (WebSocket, REST
bootstrap, REST recovery, a retry) is always harmless: an idempotent no-op,
or an intentional correction if the exchange-backed values differ.

### MARKET remembers: durable history, bootstrap, and retention

`ClosedCandle`s are now durably persisted, backfilled up to a rolling
horizon, self-repairing after gaps or outages, and locally aggregated into
15m/1h/4h/24h — all as a continuously-running background capability, never
triggered by a request. The horizon itself is one setting,
`Settings.market_history_retention_days` — this milestone's original
design target and the value used throughout the rest of this section's
examples was ~1 year (365 days); **the actual current runtime value is
~30 days** — see "Current runtime policy" at the end of this section for
why and exactly what that changed:

```
app/models/instrument.py, app/models/candle.py
   Instrument: one row per (exchange, symbol) MARKET has ever tracked,
   never deleted, carrying the three-watermark bootstrap/reconciliation
   state (history_target_start/synced_from/synced_through — see the
   model's docstring for exact semantics).
   Candle: composite PK (instrument_id, timeframe, open_time), a native
   PostgreSQL table partitioned by RANGE(open_time) in monthly partitions
   — the same table and partitioning serve every timeframe.
        │
        ▼
app/services/partition_manager.py
   Idempotent CREATE/DROP of whole monthly partitions — retention is a
   handful of DROP TABLE statements, never a row-by-row delete. A no-op on
   SQLite (tests), since only PostgreSQL is partitioned.
        │
        ▼
app/services/history_reconciler.py (HistoryReconciler)
   A second long-lived background capability, started alongside
   MarketCollector. Round-robins the active universe with bounded
   concurrency; for each instrument, one bounded step walks
   history_synced_from backward toward an *effective* target start — the
   later of the instrument's own persisted history_target_start (an
   honest record of what bootstrap was aimed at when this instrument was
   first discovered, never rewritten) and `now - retention_days` (so a
   config change that shrinks retention after an instrument was
   discovered can never send bootstrap digging toward history the current
   policy no longer promises) — and one walks history_synced_through
   forward toward "now minus a small buffer" (live catch-up/gap-repair) —
   the same REST kline endpoint,
   applied to different parts of the timeline. Catch-up first checks
   whether the live collector already filled the range before ever calling
   Bybit REST. Also periodically re-runs universe discovery (updating
   `instruments`, marking newly-inactive symbols without deleting their
   candles, and telling MarketCollector to start watching newly-eligible
   ones via `add_symbols`).
        │
        ▼
app/services/candle_ingestion.py, app/services/candle_aggregation.py
   The shared canonical-5m boundary both the live sink and the reconciler
   funnel through: upsert the 5m batch, then re-derive any 15m/1h/4h/24h
   window the batch touches — but only ones where every constituent 5m
   candle actually exists in storage. An incomplete window is silently
   left alone and completes itself whenever this is next called over a
   range that includes it (a later recovery, or a correction). Every
   derived timeframe is built one level from 5m (e.g. 48 constituent 5m
   candles for 4h, not chained through 1h) — no special-casing per
   timeframe beyond `app.domain.timeframes.TIMEFRAMES`.
```

"Historical coverage" (`app/services/historical_coverage.py`) is a pure,
read-only function of each active instrument's three watermarks: the
average, across all active instruments, of how much of
`[target_start, now]` is currently confirmed-reconciled — where
`target_start` is tightened to whichever is later of the instrument's own
discovered floor (a newly-listed symbol, or wherever Bybit's history
happens to run out) or the current rolling retention cutoff. It never
queries `market_candles` directly (no scan over hundreds of millions of
rows on every Vitals load) and never triggers any MARKET work.

**Current runtime policy: ~30 days, not ~1 year.** The mechanism above is
retention-horizon-agnostic by design — it was built once, against a ~1-year
target, and works identically at any horizon. The single knob is
`Settings.market_history_retention_days` (`apps/api/app/core/config.py`),
currently set to `30`. Lowering it (from its original `365`) controls the bootstrap target for
newly-discovered instruments (`InstrumentRepository.reconcile_universe`),
the partition-retention cutoff (`partition_manager.drop_expired_partitions`,
called for `market_candles`, `market_frame_members`, and `sniffer_results`
alike), and the "Historical coverage" denominator above. It does **not**
retroactively rewrite any instrument's already-persisted
`history_target_start` — that column stays an honest record of what
bootstrap originally aimed for; `_bootstrap_step`'s effective-target-start
clamp (described above) is what keeps that stale, deeper value from
causing new bootstrap work beyond the current 30-day policy. Partition
retention is granular to whole months (see `partition_manager.py`), so
"~30 days" is a floor, not an exact cutoff — the oldest retained data can
be anywhere from ~30 to less than 61 days old depending where "30 days ago" falls
within its month; the currently-open month's partition is never dropped
while any part of it is still within the window. Surviving frame members
can refer across month boundaries or to arbitrarily stale candles. Retention
therefore prunes expired member partitions first and only drops an expired
candle partition if no remaining member references that month. Such references
can extend candle retention beyond the ordinary monthly rounding. The check
and candle drops hold table locks to serialize with frame construction.
Catch-up also clamps long-outage work to the rolling horizon. Partition
provisioning runs on successful periodic universe refreshes as well as startup.
See `docs/HISTORY_RETENTION.md` for operational details.

### Symbol readiness: DISCOVERED -> BACKFILLING -> READY

Bootstrap/catch-up (above) and "how ready is this instrument's data,
specifically" are two different questions — the former is the mechanism,
the latter is a classification a future ranking/Score consumer should
gate on, so it only ever surfaces a symbol with real data behind it
rather than one whose Sniffs are still mostly `None` for lack of history.
`app/services/symbol_readiness.py` answers it, in the same pure,
read-only, watermark-based style as `historical_coverage.py` (never
triggers bootstrap, recovery, or any Bybit call itself):

```
app.services.symbol_readiness.compute_readiness(instrument, now, required_warmup)
   DISCOVERED   the watermark is still at (or hasn't moved back from)
                its starting value — no backward bootstrap progress
                made yet.
        │  bootstrap walks the watermark backward
        ▼
   BACKFILLING  some backward progress made, but the watermark hasn't
                yet reached back `required_warmup` before `now`.
        │  bootstrap keeps walking backward
        ▼
   READY        watermark <= now - required_warmup — enough calendar
                span of reconciled history exists for every currently-
                required feature's timeframe to be derivable.
```

A symbol is classified independently against **three** watermarks —
`history_synced_from` (candles, walked by `HistoryReconciler`),
`oi_synced_from` (Open Interest, walked by `OpenInterestReconciler` —
see "MARKET watches Open Interest" below), and `funding_synced_from`
(Funding Rate, walked by `FundingRateReconciler` — see "MARKET watches
Funding Rate" below) — and the overall result is the *least-ready* of
the three: an instrument with deep candle history but fresh-discovery OI
or funding (or any other combination) is only as ready as its laggard,
since a feature built on the lagging source would still be `None`.

Like `historical_coverage`, this is an approximation at the same
precision level: it trusts the watermarks rather than re-verifying every
individual candle/observation is gap-free — RSI/return/OI-change
features still independently return `None` for any real remaining gap,
exactly as they always have. It only decides whether an instrument has
plausibly enough history to be worth including at all.

**`required_warmup` is never hardcoded.** It's `app.features.engine.
REQUIRED_WARMUP` — the max `required_history` across every currently-
configured `Feature`, spanning candle-based, Open-Interest-based,
Funding-Rate-based, and ATR-based features alike (today, `volatility_24h`'s
15-candle/24h window — 15 days — is the longest, well past
`rsi_14_4h`'s/`relative_volume_4h`'s shared 60h and `funding_rate_24h_avg`'s
24h). Each `Feature` declares its own `required_history: timedelta`
(`ReturnFeature` — its lookback;
`RsiFeature`/`VolatilityFeature`/`RelativeVolumeFeature` — 15 × their
timeframe's duration; `OpenInterestChangeFeature` — its lookback
constant; `FundingRateCurrentFeature`/`FundingRate24hAvgFeature` — 24h);
adding a feature with a longer lookback to `FEATURES` automatically
raises `REQUIRED_WARMUP`, and with it both `OpenInterestReconciler`'s
and `FundingRateReconciler`'s bootstrap target/retention and the READY bar
everywhere `symbol_readiness` is used, with no
other change needed anywhere in the reconciliation system.

**Where this is used today:** Vitals' MARKET section ("Symbols ready",
`X / symbols_tracked`) via `summarize_readiness` — a pure in-memory
count over the same `active_instruments` list `historical_coverage`
already reads, no extra query. No ranking/Score consumer exists yet to
gate on `READY` directly (see "OINK CORP: future design" below) — this is
the hook OINK CORP's future ranking runtime would use,
not a change to Sniffer's own behavior today: Sniffer still computes
Sniffs for every frame member regardless of readiness, and still
truthfully returns `None` for whichever ones lack sufficient history,
exactly as before this existed.

### MARKET watches Open Interest

A fourth long-lived background capability, `app/services/
open_interest_reconciler.py` (`OpenInterestReconciler`), started
alongside `MarketCollector`/`HistoryReconciler` from the FastAPI lifespan
— nothing here is triggered by a request. Structurally the same
bootstrap-then-catch-up shape `HistoryReconciler` uses for candles (one
bounded REST page per instrument per round, round-robin over the active
universe with bounded concurrency), simplified because OI only needs a
short rolling window (`REQUIRED_WARMUP`-scale — currently 60h — not the
~30-day candle retention) and has no WebSocket source of its own:

```
app.integrations.bybit.client.BybitClient.get_open_interest
   GET /v5/market/open-interest, always at intervalTime=5min (the finest
   granularity Bybit offers) — MARKET's one canonical OI series, the OI
   equivalent of the 5m base candles. Bybit's public WebSocket does carry a live
   openInterest field on its `tickers` topic, but since the REST history
   endpoint already buckets at a fixed 5 minutes, polling it every 5
   minutes is exactly as fresh as that native granularity allows — a
   second, WS-based live path would add real complexity (another
   connection type, reconnect/backoff policy, sink) for freshness this
   app would never actually observe. One REST endpoint, one poll loop,
   covers both backfill and "live" updates.
        │
        ▼
app.services.open_interest_reconciler.OpenInterestReconciler
   Polls every `poll_interval_seconds` (default 300s = 5 minutes, matching
   Bybit's own bucket granularity). Each round, per active instrument:
   `_bootstrap_step` walks `Instrument.oi_synced_from` backward toward
   `now - REQUIRED_WARMUP` (one bounded page per round, same "accept an
   empty page as the natural floor" convention `HistoryReconciler.
   _bootstrap_step` uses for a brand-new listing); `_catchup_step` fetches
   forward from `MAX(observed_at)` for that instrument (via
   `OpenInterestRepository.fetch_latest_observed_at_per_instrument` — no
   separate persisted "through" watermark; OI's short window makes
   resuming from the table itself cheap) to `now`. A bad symbol's
   exception is caught and logged per-instrument, never stopping the
   round for the rest of the universe. Each round also prunes
   observations older than `retention` (default 2 days) via a plain
   `DELETE ... WHERE observed_at < cutoff`
   (`OpenInterestRepository.delete_before`) — not the monthly-partition
   machinery `partition_manager` owns, since at one row per instrument
   per 5 minutes this table stays orders of magnitude smaller than the
   partitioned candle/frame/Sniffer tables that scheme exists for.
        │
        ▼
app.repositories.open_interest_repository.OpenInterestRepository
   Same idempotent-upsert, set-oriented-batched-query conventions as
   CandleRepository: `upsert_many` (composite PK `(instrument_id,
   observed_at)` — a re-fetched observation corrects, never duplicates),
   `fetch_exact_observed_at`/`fetch_latest_at_or_before_per_instrument`
   (the OI equivalents of `fetch_exact_open_time`/
   `fetch_latest_closed_per_instrument`, used by
   `OpenInterestChangeFeature` — see "Open Interest change" above).
```

`instruments.oi_synced_from` is its own column, deliberately not reusing
`history_synced_from` — OI and candles come from different Bybit
endpoints on independent schedules, and a symbol can legitimately have
deep candle history while OI backfill still lags (or vice versa); see
"Symbol readiness" above for how the two (three, with funding) combine.
There is no `open_interest_observations` equivalent of
`history_target_start`/`history_synced_through`: OI's bounded, short
window doesn't need the retention-horizon-clamping or forward-frontier
bookkeeping candles do at ~30-day scale.

### MARKET watches Funding Rate

A fifth long-lived background capability, `app/services/
funding_rate_reconciler.py` (`FundingRateReconciler`), started alongside
`OpenInterestReconciler` from the FastAPI lifespan. Structurally
byte-for-byte the same bootstrap-then-catch-up shape as
`OpenInterestReconciler` (see above) — same reasoning, same short
`REQUIRED_WARMUP`-scale window, no WebSocket source of its own — but
against a different Bybit endpoint with a genuinely different data
shape:

```
app.integrations.bybit.client.BybitClient.get_funding_rate_history
   GET /v5/market/funding/history — unlike open-interest, this endpoint
   has no fixed bucket granularity to request: each returned entry is an
   actual settled funding event at whatever interval that instrument's
   real contract uses (commonly 1h/2h/4h/8h, confirmed live against
   production Bybit data to vary per instrument — never assumed or
   hardcoded). MARKET just walks Bybit's own reported settlement
   timestamps directly.
        │
        ▼
app.services.funding_rate_reconciler.FundingRateReconciler
   Polls every `poll_interval_seconds` (default 300s, same as OI — cheap
   headroom, not a claim that funding settles that often). Each round,
   per active instrument: `_bootstrap_step` walks
   `Instrument.funding_synced_from` backward toward
   `now - REQUIRED_WARMUP` (same empty-page-is-the-natural-floor
   convention); `_catchup_step` fetches forward from
   `MAX(funding_time)` for that instrument to `now`. A bad symbol's
   exception is caught and logged per-instrument, never stopping the
   round. Each round also prunes observations older than `retention`
   (default `required_warmup + 1 day` — see "MARKET watches Open
   Interest" above for why that default, not a fixed constant, is load-
   bearing) via a plain `DELETE ... WHERE funding_time < cutoff`.
        │
        ▼
app.repositories.funding_rate_repository.FundingRateRepository
   Same idempotent-upsert, set-oriented-batched-query conventions as
   OpenInterestRepository: `upsert_many` (composite PK `(instrument_id,
   funding_time)` — a re-fetched observation corrects, never
   duplicates), `fetch_latest_at_or_before_per_instrument` (used by
   `funding_rate_current`), `fetch_since_per_instrument` (used by
   `funding_rate_24h_avg` — every observation in a trailing window, per
   instrument, however many a given instrument's real interval yields).
```

`instruments.funding_synced_from` is its own column, for the same reason
`oi_synced_from` is: an independent Bybit endpoint, an independent
schedule, and a symbol's funding backfill can legitimately lag or lead
its candle/OI backfill.

### MARKET synchronizes: Market Frames

A candle answers "what happened?" A **Market Frame** answers "what was
knowable about the whole market at time T?" Every closed UTC 5-minute
boundary, a
third background capability turns `market_candles` (many independent
per-instrument, per-timeframe time series) into one synchronized
cross-sectional snapshot future Sniffer can consume without re-deriving
temporal alignment itself:

```
app/services/frame_synchronizer.py (FrameSynchronizer)
   Wakes exactly on UTC 5-minute boundaries. At frame_time = T: snapshots the
   *active* universe right then (fixing expected_instruments immutably —
   a later universe change never rewrites an already-finalized frame's
   expectations), persists a BUILDING row immediately (crash-safe), waits
   a configurable grace period (default 15s — persisting the whole
   just-closed 5m candle burst measured 8-11s after the boundary) so each
   instrument's newest candle is stored in time, then finalizes — never on
   the first symbol to arrive. It always builds exactly the boundary it
   slept toward, never one re-derived from the clock after waking (an
   asyncio timer firing a hair early used to floor the frame back to the
   previous boundary).
        │
        ▼
app/repositories/candle_repository.py
   fetch_latest_closed_per_instrument(timeframe, instrument_ids, max_open_time)
   One set-oriented query (a portable ROW_NUMBER() OVER (PARTITION BY
   instrument_id ORDER BY open_time DESC) window function — works
   identically on SQLite in tests and PostgreSQL in production) resolves
   the whole active universe's latest *legal* candle for one timeframe.
   Called once per configured timeframe (five total: 5m/15m/1h/4h/24h) —
   never once per instrument. `max_open_time` is `frame_time - duration`,
   which is exactly equivalent to "close_time <= frame_time" given how M3
   already defines close_time, without needing an index on close_time at
   all.
        │
        ▼
app/repositories/frame_repository.py (FrameRepository)
   An instrument is a frame "member" only if all three *gating* timeframes
   (5m/15m/1h) resolved — missing even one means no member row, never a
   fabricated placeholder. 4h and 24h are resolved the same no-look-ahead
   way but do not gate membership (see "4h/24h: two non-gating timeframes"
   below). Batch-inserts members and flips BUILDING ->
   COMPLETE (available == expected) or PARTIAL (available < expected),
   guarded so a second finalize attempt (a duplicate trigger, or
   late-arriving recovered data) can never rewrite an already-terminal
   frame — a finalized frame is an immutable statement of what MARKET
   actually had available at T.
```

**Frame time.** `frame_time = T` (always a 5-minute boundary) means "the
decision point immediately after the 5m candle `[T-5m, T)` closed." A
frame may only reference candles that have closed by `frame_time` — e.g.
at `frame_time=14:35`, `m5` is `14:30-14:34`, and the legal 1h
candle is `13:00-13:59` (`14:00-14:59` is still forming, so it can never be
selected); the legal 4h candle is `08:00-11:59` (`12:00-15:59` is still
forming); the legal 24h candle (on that same day) is the *previous* day's
`00:00-23:59` UTC bucket, since today's own hasn't closed yet. Verified
directly against real data in this milestone's live smoke test, and by
dedicated tests in `tests/test_frame_4h.py`/`tests/test_frame_24h.py` for
the 4h/24h cases specifically.

**Schema.** `market_frames` (frame_time as PK — no surrogate id; small,
~105,000 rows/year at one per 5 minutes, not partitioned) and `market_frame_members`
(`(frame_time, instrument_id)` composite PK, partitioned by
`RANGE(frame_time)` exactly like `market_candles` — same
`app.services.partition_manager`, generalized to take a `table` parameter
rather than duplicated). A member stores five `open_time` datetimes (the
identifying half of `market_candles`' own `(instrument_id, timeframe,
open_time)` key, `timeframe` implied by the column) — never duplicated
OHLCV; `open_time_4h`/`open_time_24h` are nullable (see below), the other
three are not (plus the legacy, unmapped `open_time_1m` — see "Canonical
base timeframe" above). The dominant "give me frame T" read joins back to
`market_candles` five times (one per timeframe, the 4h/24h joins are LEFT
JOINs); "give me the latest finalized frame" is the same, keyed off
`MAX(frame_time) WHERE status != 'building'`.

**4h/24h: two non-gating timeframes.** `MarketFrameMember.h4`/`h24`
(`app/domain/frame.py`) are each `None` — never fabricated — for two
honest reasons: a member finalized before that timeframe's tracking
existed (`open_time_4h` was added by migration `b53245e910f5`,
`open_time_24h` by `843a62ca44e6` — both nullable specifically so
historical rows need no invented backfill value), or an instrument whose
first bucket for that timeframe genuinely hasn't closed yet (a 24h bucket
takes up to 24x longer to first become available than 1h — much longer
than 4h's own up-to-4x delay). Gating COMPLETE/PARTIAL on either would
make newly-listed instruments PARTIAL for hours (up to a full day for
24h) for a reason unrelated to their actual live-data health, so neither
is part of the completeness contract; only `rsi_14_4h` and the
`volatility_*` features (see "Sniffer measures" below) depend on either
being present, and are `None` when it isn't.

**Restart.** A process that crashes between creating a BUILDING row and
finalizing it leaves that one row inspectable, never silently corrupt. On
the next startup, `FrameSynchronizer` finalizes exactly that one
interrupted frame (safe at any delay — the temporal bound is on
`frame_time`, not on when the query runs) and resumes the 5-minute loop
from there; it never fabricates frames for whatever boundaries were missed
while the process was down.

Vitals reads two new values — "Latest market frame" and "Frame
completeness" — purely from `FrameRepository.get_latest_finalized()`;
opening Vitals never creates, advances, or otherwise drives a frame.

### Sniffer measures: Returns, RSI, OI change, Volatility, Relative Volume, Funding Rate

Sniffer's capability is deliberately narrow: consume one finalized Market
Frame, compute factual metrics per instrument member, and persist them.
Nothing here ranks, scores, or interprets — it only measures. Today's 23
Sniffs:

- **Returns** — `return_5m`, `return_15m`, `return_1h`, `return_4h`,
  `return_24h`.
- **RSI(14)** — `rsi_14_5m`, `rsi_14_15m`, `rsi_14_1h`, `rsi_14_4h`
  (deliberately no `rsi_14_24h` yet).
- **Open Interest change** — `oi_change_5m`, `oi_change_15m`,
  `oi_change_1h`, `oi_change_4h`, `oi_change_24h`.
- **Volatility (ATR%)** — `volatility_15m`, `volatility_1h`,
  `volatility_4h`, `volatility_24h` (deliberately no `volatility_5m` yet)
  — descriptive only, no scoring/desirability judgment.
- **Relative Volume** — `relative_volume_15m`, `relative_volume_1h`,
  `relative_volume_4h` (deliberately no `relative_volume_24h` yet) —
  current-window volume over the mean of the 14 preceding equivalent
  windows, as a plain ratio (1.0 == normal), descriptive only.
- **Funding Rate** — `funding_rate_current`, `funding_rate_24h_avg` —
  sourced from real settled perpetual funding events (see "MARKET
  watches Funding Rate" above), signed like Returns/OI change (a zero or
  negative reading is a legitimate value, never "unavailable").

("Sniff" is the product term for what the backend still calls a Feature —
`app/features/`, `FeatureEngine`, the `Feature` base class below — those
internal names are unchanged; only the UI-facing label is renamed.)

```
app/services/frame_synchronizer.py (FrameSynchronizer._finalize_frame)
   After flipping a frame COMPLETE/PARTIAL, calls an optional
   on_frame_finalized(frame_time) hook — wrapped in its own try/except, so
   a Sniffer failure can never break MARKET's own finalize step.
        │
        ▼
app/services/sniffer.py (Sniffer.on_frame_finalized -> analyze_frame)
   Re-fetches the finalized frame by frame_time via FrameRepository
   (never trusts a passed-in object — always re-reads the persisted,
   authoritative record). Skips silently if the frame doesn't exist or is
   still BUILDING (a defensive no-op, not an error: the hook can only ever
   fire after finalization, but analyze_frame stays safe standalone too).
   Wraps the whole analysis+persist step in try/except and logs, never
   raises — this is the second, independent layer of the same isolation
   guarantee the hook itself has.
        │
        ▼
app/features/engine.py (FeatureEngine.run)
   Orchestration only, no formulas. Iterates the fixed FEATURES tuple —
   one ReturnFeature instance per lookback (5m, 15m, 1h, 4h, 24h — the
   same "one class, many timeframe instances" pattern RsiFeature uses),
   one RsiFeature instance per
   timeframe (5m, 15m, 1h, 4h), one OpenInterestChangeFeature instance per
   timeframe (5m, 15m, 1h, 4h, 24h), one VolatilityFeature instance per
   timeframe (15m, 1h, 4h, 24h), one RelativeVolumeFeature instance per
   timeframe (15m, 1h, 4h), and FundingRateCurrentFeature/
   FundingRate24hAvgFeature (no timeframe parameter — only one of each) —
   and asks each Feature to calculate itself cross-sectionally over every
   member of the frame at once, assembling a SnifferFrameResult keyed by
   instrument. Takes a CandleRepository, an OpenInterestRepository, and a
   FundingRateRepository; only OpenInterestChangeFeature and the two
   Funding Rate features use the latter two (every other feature's
   `calculate` accepts them only for Liskov-compatible typing and ignores
   them — see app/features/base.py).
        │
        ▼
app/features/return_feature.py (ReturnFeature, one instance per lookback)
   return_{timeframe} = (current_close / close_{timeframe}_ago) - 1
   Uses Decimal. "current_close" is the frame member's own selected m5
   candle (never a fresh market_candles query — no look-ahead is even
   possible by construction). The historical anchor is the 5m candle at
   member.m5.open_time minus the labeled lookback, resolved via one
   batched exact-open_time lookup (CandleRepository.fetch_exact_open_time)
   per distinct anchor timestamp across the whole frame for each feature —
   one query when members share the same anchor, never an individual
   query loop. Missing that exact candle (or a non-positive historical
   close) yields None — "unavailable" — never a substituted, zeroed, or
   nearest-candle value.
        │
        ▼
app/features/rsi.py (RsiFeature, one instance per timeframe)
   rsi_14_{5m,15m,1h,4h} — see "RSI features" below.
        │
        ▼
app/features/open_interest_change.py (OpenInterestChangeFeature, one instance per timeframe)
   oi_change_{5m,15m,1h,4h,24h} — see "Open Interest change" below.
        │
        ▼
app/features/volatility.py (VolatilityFeature, one instance per timeframe)
   volatility_{15m,1h,4h,24h} — see "Volatility features" below.
        │
        ▼
app/features/relative_volume.py (RelativeVolumeFeature, one instance per timeframe)
   relative_volume_{15m,1h,4h} — see "Relative Volume features" below.
        │
        ▼
app/features/funding_rate.py (FundingRateCurrentFeature, FundingRate24hAvgFeature)
   funding_rate_current, funding_rate_24h_avg — see "Funding Rate
   features" below.
```

The hourly return uses canonical 5m candles exactly 60 minutes apart, not
the frame's closed hourly OHLC reference. All measurements are factual — decimal fractions or a
plain 0-100 index, never desirability or trade signals. The API and
Sniffer's Market Data pages expose all of them; older stored analyses without a
given metric expose it as null/N/A. There is no historical re-analysis.

**RSI features (`rsi_14_5m`/`rsi_14_15m`/`rsi_14_1h`/`rsi_14_4h`).**
Standard RSI(14), one per canonical timeframe, computed from the
materialized MARKET candles of that timeframe (never reconstructed inside
Sniffer) — implemented once in `app/features/rsi.py` (`compute_rsi_14` +
one reusable `RsiFeature` class instantiated four times in
`app.features.engine.FEATURES`, not four duplicated algorithms).

*Convention: Cutler's RSI*, not Wilder's original recursive smoothing.
Average gain/loss is a plain, unweighted mean over the most recent 14
price changes (15 consecutive closes):

```
change_i = close_i - close_(i-1), for the 14 most recent closes
avg_gain = mean(change_i for change_i > 0, else 0)   over 14 changes
avg_loss = mean(-change_i for change_i < 0, else 0)  over 14 changes
RS  = avg_gain / avg_loss
RSI = 100 - 100 / (1 + RS)
```

Edge cases: `avg_loss == 0 and avg_gain > 0` -> RSI = 100 (no losses in the
window at all); `avg_gain == 0 and avg_loss == 0` -> RSI = 50 (every close
in the window was flat — genuinely neutral, not fabricated as either
extreme). Wilder's convention was deliberately not used: its recursive
smoothing carries every prior period's average forward indefinitely, so
its value depends on wherever smoothing happened to start — there is no
principled "correct" starting point for a value computed fresh per frame.
Cutler's variant is fully determined by exactly the last 15 consecutive
closes, giving RSI the same determinism guarantee `return_5m`/`return_1h`
already have.

*History requirement.* Exactly 15 consecutive closed candles of the named
timeframe, each exactly one timeframe-duration apart, ending exactly at
the frame's already-selected anchor candle for that timeframe
(`member.m5`/`m15`/`h1`/`h4`). Fewer than 15, any gap, or a missing anchor
(`h4` not yet available for that instrument/frame — see "4h: a fifth
timeframe, deliberately non-gating" above) all yield `None` — never a
shortened period, an interpolated value, or the nearest available candle
substituted in. `CandleRepository.fetch_latest_n_closed_per_instrument`
(the batched, set-oriented "N most recent legal candles per instrument"
query RSI needs, generalizing `fetch_latest_closed_per_instrument`'s
`rn == 1` to `rn <= n`) fetches the candidate window; `RsiFeature` then
verifies the fetched open_times are *exactly* the expected 15 consecutive
timestamps before ever computing anything — a superficially-sufficient
count with a gap in the middle is rejected just as surely as too few rows.

*No look-ahead.* Each timeframe's 15 closes are anchored to the exact
`open_time` Market Frame T already selected for that timeframe — never a
fresh "latest candle" query — so RSI inherits the frame's no-look-ahead
guarantee for free, exactly like `return_5m`/`return_1h`. A candle that
arrives later than a frame's own anchor (a newer close, or a whole newer
4h bucket) can never change that frame's already-computed RSI.

*Batching.* Members are grouped by identical anchor `open_time` before
querying (the common case — every member sharing the same frame-relative
anchor — collapses to one query per timeframe, never one per instrument),
mirroring `return_5m`/`return_1h`'s existing batching pattern exactly.

**Open Interest change (`oi_change_5m`/`15m`/`1h`/`4h`/`24h`).**

```
oi_change_{timeframe} = (current_oi / previous_oi) - 1
```

Stored as the same raw decimal fraction convention as `return_*` (`0.05`
== +5%), never pre-multiplied by 100 — so every existing "signed
percentage" presentation convention (frontend formatting, sign coloring)
applies unchanged; only the underlying source (Open Interest observations
instead of candle closes) differs. See "MARKET watches Open Interest"
below for where those observations come from.

"Current OI" is the latest Open Interest observation at or before
`frame.frame_time` (`OpenInterestRepository.
fetch_latest_at_or_before_per_instrument`) — the OI equivalent of a
frame's already-selected candle anchor, so this inherits the frame's
no-look-ahead guarantee exactly like every other feature. "Previous OI"
is the observation at exactly `current.observed_at - lookback` — a
precise time offset from that anchor (never "now - lookback"), mirroring
`ReturnFeature`'s own anchor-then-exact-offset pattern. Missing either
observation (a gap, insufficient OI backfill, or the instrument simply
not tracked that far back) yields `None` — never approximated or
interpolated. `OpenInterestChangeFeature.calculate` requires a real
`oi_repo` and raises (rather than silently returning all-`None`) if one
isn't given — `FeatureEngine.run` always supplies one; only a direct
unit test of a candle-only feature would ever omit it.

Bybit's own `/v5/market/open-interest` data is bucketed at a fixed
5-minute granularity (the finest it offers) — every OI Sniff timeframe
(5m/15m/1h/4h/24h) is an exact multiple of 5 minutes, so one canonical
5-minute-bucket series serves all of them via exact-timestamp lookup,
the same one-canonical-series-derives-every-timeframe shape
`market_candles`' 5m→15m/1h/4h/24h derivation uses, just without needing
any local aggregation step.

**Volatility features (`volatility_15m`/`1h`/`4h`/`24h`).** Normalized
ATR(14) — descriptive only, no scoring/desirability judgment:

```
ATR(14) = mean(TR_i for the 14 most recent True Range values)
TR_i = max(high_i - low_i, |high_i - close_(i-1)|, |low_i - close_(i-1)|)
volatility_{timeframe} = ATR(14) / current_price
```

Stored as the same raw decimal fraction convention as `return_*`/
`oi_change_*` (`0.05` == 5%), but — unlike either — always non-negative:
volatility has no direction, so the UI never shows it with a sign.
`current_price` is the anchor candle's own close (the most recent of the
15 consecutive candles the window is built from) — the same "current" any
other timeframe-anchored feature uses, never a separately-fetched price.

*Convention: a plain, unweighted mean of the 14 most recent True Range
values* — not Wilder's original recursive smoothing — for exactly the
same reason RSI uses Cutler's convention (see "RSI features" above):
recursive smoothing depends on wherever it happened to start, with no
principled "correct" starting point for a value computed fresh per frame.
This "simple ATR" is fully determined by exactly the last 15 consecutive
closed candles of the given timeframe — the identical history requirement
RSI has (implemented via a shared helper, `app/features/candle_window.py`
— `fetch_verified_windows`/`expected_window`/`REQUIRED_CLOSES` — rather
than duplicating RSI's own equivalent logic; RSI's own copy was
deliberately left untouched, since it's stable and already extensively
tested, and there was no functional reason to refactor it just to
consolidate). Fewer than 15 candles, a gap, or a missing anchor (e.g.
`h24` not yet available) all yield `None` — never a shortened period, an
interpolated value, or the nearest available candle.

`volatility_24h` needs 15 consecutive *24h* candles, which is exactly why
24h joined 4h as MARKET's second non-gating timeframe (see "4h/24h: two
non-gating timeframes" above) — Sniffer's own architectural rule (see
`app/features/base.py`'s docstring) is that a feature's notion of
"current"/historical context must always come from data already selected
into the frame, never a fresh independent `market_candles` query; there
was no way to honor that rule for a 24h-granularity feature without a
real `member.h24` anchor to build on.

**`REQUIRED_WARMUP` after Volatility.** `volatility_24h`'s 15-candle/24h
window needs 15 days of reconciled *24h-candle* history — the deepest
lookback any feature has needed so far, well past `rsi_14_4h`'s previous
60h high-water mark. `app.features.engine.REQUIRED_WARMUP` picks this up
automatically (it's still just `max(f.required_history for f in
FEATURES)`), and two things downstream automatically follow suit with no
code change needed: `app.services.symbol_readiness`'s READY bar (a symbol
now needs 15 days of *all three* — candle, OI, *and* funding — history to
read READY), and `OpenInterestReconciler`'s/`FundingRateReconciler`'s own
default retention, each derived from `required_warmup` (see "MARKET
watches Open Interest"/"MARKET watches Funding Rate" above) specifically
so a growing `REQUIRED_WARMUP` can never make either reconciler prune
bootstrap progress it just walked back to.

**Relative Volume features (`relative_volume_15m`/`1h`/`4h`).** Current-
window volume relative to this instrument's own recent normal volume for
an equivalent window — a plain ratio, never a percentage, and unsigned
like Volatility (no green/red styling — "high vs low" participation is a
descriptive fact, not a signal):

```
relative_volume_{timeframe} = current_volume / baseline_volume
current_volume  = window[-1].volume                    (the anchor candle itself)
baseline_volume = mean(c.volume for c in window[:-1])   (the 14 candles before it)
```

Deliberately reuses Volatility's exact same "15 consecutive closed
candles ending at the frame's already-selected anchor" window (the same
`app/features/candle_window.py` helper, the same batching, the same
no-look-ahead/insufficient-history behavior) rather than a separate
windowing implementation — only how the window is *read* differs: ATR
treats all 15 as one continuous series, RVOL splits the newest one off as
"current" and averages the other 14 as the baseline. A `baseline_volume`
of exactly zero (a newly listed, untraded instrument) yields `None`
rather than an infinite or fabricated ratio. Deliberately no
`relative_volume_24h` yet.

**Funding Rate features (`funding_rate_current`/`funding_rate_24h_avg`).**
The first Sniffer feature family that needed its own real ingestion
pipeline rather than reading data another capability already collects —
built by mirroring `OpenInterestReconciler`'s architecture file-for-file
(see "MARKET watches Funding Rate" above): `funding_rate_observations`
(composite PK `(instrument_id, funding_time)`, idempotent upsert),
`Instrument.funding_synced_from` (its own backward-bootstrap watermark,
independent of `oi_synced_from`), `FundingRateReconciler` (bootstrap +
catch-up + retention, started from the lifespan alongside
`OpenInterestReconciler`).

```
funding_rate_current  = latest observation at-or-before frame.frame_time
funding_rate_24h_avg  = mean(observations with frame_time - 24h <= funding_time <= frame_time)
```

Both read `funding_rate_observations` directly against `frame.frame_time`
— never a frame member's own candle anchor, since funding isn't a
candle-derived measurement — but stay fully no-look-ahead-safe: every
query is bounded to "at or before"/"no later than" `frame.frame_time`,
the same guarantee `OpenInterestChangeFeature` gets from
`OpenInterestRepository`. Stored as the same raw-fraction convention as
`return_*`/`oi_change_*` (`0.0001` == +0.01%), signed — unlike Volatility,
funding pressure has a real direction — and a zero or negative reading is
a completely ordinary, legitimate value, never treated as "unavailable"
(unlike Open Interest, which requires a positive divisor).

Each instrument's real Bybit funding interval (commonly 1h/2h/4h/8h,
confirmed live against production data to genuinely vary per instrument)
is never assumed or hardcoded: `funding_rate_24h_avg` simply averages
however many real settlements actually fall in the trailing 24h window
for that instrument — a 1h-interval instrument naturally contributes more
points than an 8h-interval one over the same 24 hours, and that's
correct, not something to normalize away. `None` only when the relevant
window has no observations at all.

**Result model and persistence.** `SnifferFrameResult`/
`SnifferInstrumentResult` (`app/domain/sniffer.py`) are the typed in-memory
result — `features: dict[str, Decimal | None]`, explicit about
unavailability. `SnifferRepository.save_result` persists every instrument
(including unavailable ones, as a NULL `value` row — Sniffer looked at it
and recorded that fact, rather than omitting it) into `sniffer_results`
(`frame_time, instrument_id, metric, value, calculated_at`), an
intentionally EAV-lite shape: a second metric is a new row shape, never a
schema migration. A dialect-aware upsert on the composite
`(frame_time, instrument_id, metric)` primary key makes re-analysis
idempotent — a retry or duplicate trigger overwrites, never duplicates.
`RANGE(frame_time)`-partitioned exactly like `market_frame_members`, same
`partition_manager`, same rolling retention window (currently ~30 days —
see "Current runtime policy" under "MARKET remembers" above). Unified
deliberately: `market_frame_members` rows are meaningless once the
candles they reference have aged out of `market_candles`, so it *must*
track the same horizon; `sniffer_results` rows are self-contained
(an already-computed value, not a reference) and so aren't forced to, but
sharing one policy across all three partitioned tables stays the simplest
correct choice unless a real requirement for Sniffer to outlive candle
retention ever emerges.

**Reactive, not polling.** Sniffer has no loop of its own; it only ever
runs in direct response to `FrameSynchronizer`'s hook, mirroring the
`on_new_symbols` pattern `HistoryReconciler`/`MarketCollector` already use.
This keeps Sniffer decoupled (`FrameSynchronizer` knows only that an async
callable exists, nothing about Sniffer's internals) while guaranteeing it
never falls behind by polling on some independent cadence.

**API and UI.** `/api/sniffer/status` (Vitals), `/api/sniffer/latest`, and
`/api/sniffer/frames/{frame_time}` are read-only — no endpoint triggers
analysis. Responses are always sorted by symbol, a neutral ordering that
implies no ranking. These endpoints and their response shape
(`SnifferFrameResponse`/`SnifferInstrument`, see `app/schemas/sniffer.py`)
represent **Sniffs** — factual measurements — and stay that way regardless
of what any consumer does with them; see "Sniffer in the UI: Market Data"
below. A client-side column sort on the
Sniffs matrix re-orders only what's already on the page for that viewer and
never calls the backend, so it can't be mistaken for a server-side ranking.

### Sniffer in the UI: Market Data

**Sniffs** is the product term for Sniffer's features: factual,
per-instrument measurements — facts, never judgments (e.g. `rsi_14_5m = 25`
is a measurement; "low RSI after a sharp decline favors Longs" is a
heuristic, which belongs to OINK CORP). The backend keeps the engineering
names (`app/features/`, `FeatureEngine`, metric keys like `rsi_14_5m`);
"Sniff" is only how a feature is presented to the user.

Two routes under `apps/web/src/app/(protected)/sniffer/`, sharing one
shell (`layout.tsx`, titled "🐽 Sniffer / Market Data"):

```
/sniffer             Market Data inspector (SnifferSniffs): latest frame
                      context, "Inspect symbol…" search, and the full
                      Sniffs matrix (SnifferTable) over the tracked universe
/sniffer/{symbol}     Symbol detail (SnifferSymbolDetail): that symbol's
                      Sniffs, grouped like the matrix
```

Both read the same `getLatestSnifferFrame()` and the same
`FEATURE_COLUMNS` config, so the matrix and a symbol page can never
disagree; no extra backend endpoint exists for either. Missing values
render as "N/A", never 0. An unknown or not-yet-analyzed symbol renders an
honest inline message.

Sniffer's UI deliberately has no Score, Rank, Scents, or 🍄 Truffle
surface. An earlier iteration made `/sniffer` a Green/Red "Truffles"
dashboard and gave symbol pages Scents/Score/Rank sections; those were
always-empty placeholders attributing ranking to Sniffer, and were removed
when ranking ownership moved to OINK CORP. `/sniffer/sniffs` (the matrix's
old route) and `/sniffer/truffles` redirect to `/sniffer`
(`next.config.ts`). Ranking views will be built under OINK CORP once a
Champion model actually produces rankings — not before.

### OINK CORP: historical Sniff replay and research datasets

The first real OINK CORP capability: answering "given everything Sniffer
could have known about instrument X at historical time T, what happened to
it afterward?" — the precondition for ever validating a ranking
contribution, not a ranking itself. Lives in `apps/api/app/research/` (tested/typed alongside the
rest of the backend, imported by nothing production — never the reverse)
and `app/scripts/generate_research_dataset.py`, a batch/offline CLI in the
same `python -m app.scripts.X` family as `backfill_derived_timeframes.py`.

```
app.research.replay.replay_frame(session, frame_time)
   FrameRepository.get_frame(frame_time) + FeatureEngine.run(...) — the
   exact same two calls Sniffer.analyze_frame makes for "now", just for a
   frame_time in the past. This is what makes production-vs-research Sniff
   parity structural rather than maintained: see test_replay.py's parity
   test. Answers "what would TODAY's FeatureEngine measure at T" — not "what
   did the historical software version compute at T" (feature definitions
   evolve; there is no historical code-version execution). Mirrors
   Sniffer's own BUILDING-skip rule. Only 5-minute-boundary frame_times
   are accepted (ValueError otherwise).
        │
        ▼
app.research.forward_returns.compute_forward_returns / compute_forward_relative_returns
   The ONLY place in the codebase allowed to read data after frame_time —
   never a Feature, never in FEATURES, never persisted to sniffer_results.
   forward_return_H = P(T+H) / P(T) - 1, where P(X) is the close of the 5m
   candle closing exactly at X: P(T) is member.m5.close, and only when
   member.m5 is the candle opened at T-5m (a stale anchor after a gap
   yields null for every horizon rather than silently stretching the
   label); P(T+H) is the 5m candle opened at T+H-5m — not the one opened
   at T+H, which closes 5 minutes too late. Example: T=10:05, H=15m uses
   the 10:00 candle's close and the 10:15 candle's close (at 10:20). H must
   be a positive multiple of 5m; anything else is rejected, never rounded.
   forward_relative_return_H subtracts the cross-sectional median
   forward_return_H (across that same frame's own members with a real
   label — never today's active universe, never a mean, which crypto's
   extreme single-instrument tail moves would dominate with no market-cap/
   liquidity weighting data available to justify weighting one way or another).
        │
        ▼
app.research.dataset.generate_dataset
   Samples a wall-clock-aligned grid of frame_times (anchored to absolute
   epoch-minute divisibility, never to the requested start — so widening a
   range never shifts which boundaries get sampled). The interval must be a
   positive multiple of 5 minutes, so every grid point is a 5m boundary.
   Skips and counts any grid point with no real market_frames row (never
   substitutes a neighbor), replays + labels each one, and writes Parquet +
   manifest.json to a sibling temp directory before an atomic rename into
   place — a failed run never leaves a directory that looks like a
   finished dataset.
```

**Retention is the real ceiling here, deliberately not solved by this
capability.** Full 23-Sniff replay parity is bounded by whichever
production retention window is tightest — currently Open Interest/Funding
Rate's 16 days (`REQUIRED_WARMUP + 1 day`, no partition-protection, a plain
`DELETE`), with pure candle-based Sniffs reaching further (~30-61 days,
monthly-partition-rounded). No second historical database, no archival
infrastructure, and no production retention change were added to work
around this — the manifest instead makes the ceiling visible:
`frame_times_candidate` vs. `frame_times_processed`/`_missing`, and a
per-column non-null coverage fraction across the whole dataset, so a
successful run can never be mistaken for "OINK CORP now has unlimited
historical raw-data replay." Extending durable research history is
explicitly future-milestone work.

**Dataset compatibility.** Datasets carry `dataset_schema_version` (now
`2`), `base_timeframe` (`"5m"`) and the exact `label_definition`, both in
`manifest.json` and in the Parquet file's own schema metadata.
`app.research.dataset.read_dataset` refuses anything else with
`IncompatibleDatasetError`. Datasets generated before the 5m migration
(version 1: sampled at any minute, 1m-based labels) carry no version and
are refused — regenerate them rather than mixing them with v2 data. Bump
the version whenever a feature or label definition changes meaning.

Generate a dataset: `docker compose exec api python -m app.scripts.
generate_research_dataset --start 2026-09-01T00:00:00Z --end
2026-09-27T00:00:00Z --sample-interval-minutes 15 --horizons 15m,1h,4h,24h
--output research_v1` — writes to `Settings.research_data_dir`, bind-mounted
to `research/oink_corp/data/` on the host (gitignored; regenerable, never
committed). No resumability/checkpointing by design: generation is a pure,
cheap, fully regenerable read pipeline bounded by a short retention window,
so "re-run the whole command" is simpler and more honest than partial-state
recovery machinery.

This is research only, an offline batch tool — not the future live
ranking runtime, which must never depend on it. The boundary is enforced:
`tests/test_architecture_boundaries.py` fails if anything outside
`app/research` (and its CLI) imports research code, so labels can't reach
live Sniffs. Not implemented: heuristics, hypotheses, experiments,
evaluation, models, Score, Rank, Qualification, Truffles, backtesting,
Martingale simulation, a research UI, or research API endpoints. Sniffs stay in their native factual units throughout (fractions,
0-100, ratios) — no 0-1 transform exists yet.

### OINK CORP: future design

**Everything in this section is design intent, not implemented code.** The
only OINK CORP code that exists is the research foundation above (replay,
forward-return labels, versioned datasets). No heuristic, hypothesis,
experiment, evaluator, model, Champion, Challenger, Score, Rank or Truffle
exists. None should be scaffolded ahead of a real need — no placeholder
directories, tables, endpoints or pages.

#### Heuristics are first-class research inputs

A **heuristic** is a human intuition about market behavior or opportunity
attractiveness, in natural language — for example: "coins that have fallen
aggressively while selling pressure is weakening might be attractive Long
candidates", "a pump without matching open-interest growth may favor
Shorts", "Long entries become dangerous when BTC is crashing". A heuristic
is not a feature (`rsi_14_5m = 25` is a feature; "low RSI after a sharp
decline may favor Longs" is a heuristic), and it is not a trading rule —
it is a research idea that may be rejected.

Research should eventually interpret a heuristic, translate it into
testable hypotheses over available features, propose and run experiments,
evaluate them, challenge the original idea, look for conditional versions
("…more useful when selling volume is decreasing"), and preserve findings
and history. The user steers research without writing formulas or
thresholds. A heuristic's eventual outcome may be a positive or negative
ranking contribution, a risk filter, a conditional adjustment — or no
validated contribution at all.

#### Two research sources, one standard

- **Human-guided research** starts from user heuristics.
- **Autonomous research** generates its own hypotheses (feature
  relationships, conditional and regime-dependent behavior, Long/Short
  asymmetries, interactions, conditions that reduce bad outcomes, ways to
  simplify an existing model).

Both are held to exactly the same evaluation standard. The user's
confidence in a heuristic may raise its research *priority*; it must never
influence ranking weights or promotion.

#### Features: available, research-enabled, Champion-selected

Three distinct sets, all drawn from Sniffer's single feature
implementation:

- **Available** — what Sniffer computes (`app.features.engine.FEATURES`).
- **Research-enabled** — what OINK CORP research is allowed to investigate.
  Today this is simply every available feature: a dataset includes every
  `FEATURES` column.
- **Champion-selected** — what the approved live model uses. Empty today;
  no model exists.

Feature math lives only in Sniffer. Research (via replay) and the future
ranking runtime both consume Sniffer's computed values and never
re-implement them, so research and live behavior can't drift. No feature
registry or plugin system exists or is planned until a real need appears.

#### Scientific integrity

Research must prefer validity over attractive backtests:

- point-in-time correctness and no future leakage (already enforced for
  replay and labels — see above);
- chronological validation, evaluation across different market regimes,
  appropriate statistical baselines, honest uncertainty, and acceptance of
  negative results;
- reproducible, explicitly defined experiments with recorded provenance
  (datasets already carry version, code commit and definitions);
- model simplicity where possible.

Future research separates a **Researcher** (proposes hypotheses and
experiments) from an **Evaluator** (independently judges results). The
Researcher cannot redefine evaluation standards to improve its apparent
performance. Repeated experimentation against the same validation data
overfits even without ML, so the eventual system needs protected holdout
data and evaluation budgets. None of this is implemented.

#### Trading objectives are path-dependent

Warhog's eventual strategy is Martingale-inspired, so an attractive
opportunity depends on more than direction: adverse price movement,
recovery potential, volatility, liquidity, drawdown, time to recovery,
market regime and path-dependent risk. Fixed-horizon forward returns (the
only labels today) are a useful starting point, not the definition of a
good opportunity. Additional outcome labels belong beside them in
`app.research`, versioned like the existing ones.

#### Live ranking

```
current Sniffer observations (the latest Market Frame's Sniffs)
        ▼
OINK CORP Ranking Runtime — the approved Champion model, deterministic
        ▼
Long Score / Short Score → Long Rank / Short Rank
        ▼
Qualification → 🍄 Truffles (Green = Long, Red = Short)
        ▼
Warhog
```

Settled concepts from earlier design work, now owned by OINK CORP Ranking:

- **Long and Short Scores are independent** (each in `[0, 1]`): a coin can
  score high on both, low on both, or anything in between.
- **Rank** orders the analyzed universe per direction by that direction's
  Score.
- **Qualification** turns Score + Rank into a yes/no per direction. The
  V1 conceptual rule is `Score >= minimum_score_threshold AND Rank <= 10`.
  The threshold is undecided — it is a research question. Top 10 is a
  cap, never a fill target: zero qualifiers means zero Truffles, never a
  lowered bar.
- **A Truffle** is a qualified symbol/direction — a classification, not
  another metric.
- **Explainability is mandatory; weights are optional.** A model must
  expose how its output was obtained, but a nonlinear or multivariable
  model may have no honest per-feature weight, and none should be
  fabricated. Intermediate interpreted dimensions (earlier called
  "Scents") are one possible model shape, not a required layer.

The runtime must be deterministic, must not need an LLM, must not run
experiments, and must keep running whatever state research is in.
**Research has knobs; ranking has gauges:** parameters and candidate
formulas are adjusted in research, never casually in the live ranking
surface.

#### Champion / Challenger lifecycle

```
Research hypothesis → Experiment → Evaluation → Challenger model
  → Independent validation → Promotion decision → Champion → Live ranking
```

The **Champion** is the currently approved ranking model; **Challengers**
are candidates. Promotion initially requires explicit human approval —
never automatic. Every number that affects whether something becomes a
Truffle should be traceable to the promoted Champion and the research that
validated it. No model, registry or promotion workflow exists, and no
fake Champion or Challenger may be created to make this look implemented.

**Where future code would go** (not created until needed): research stays
in `apps/api/app/research/`; a ranking runtime would be a separate,
research-independent module consuming Sniffer's persisted observations;
OINK CORP's UI would gain Research and Rankings views under `/oink-corp`
once each has something real to show.

## What's actually implemented (this milestone)

```
┌─────────────┐      cookie-forwarding       ┌─────────────┐      SQLAlchemy 2      ┌─────────────┐
│   Browser    │ ───────────────────────────▶ │  apps/web    │ ─────────────────────▶ │  apps/api    │
│              │ ◀─────────────────────────── │  (Next.js)   │ ◀───────────────────── │  (FastAPI)   │
└─────────────┘                               └─────────────┘                         └──────┬──────┘
                                                                                                │ psycopg (async)
                                                                                                ▼
                                                                                        ┌──────────────┐
                                                                                        │  postgres     │
                                                                                        │  (internal    │
                                                                                        │   network     │
                                                                                        │   only)       │
                                                                                        └──────────────┘
```

- **`apps/web`** (Next.js, TypeScript, Tailwind, shadcn/ui): the application
  shell. A `(protected)` route group enforces auth server-side before
  rendering; a runtime route handler at `src/app/api/[...path]/route.ts`
  proxies `/api/*` to the backend so the browser only ever talks to one
  origin. `/sniffer` is Sniffer's Market Data inspector (the Sniffs
  matrix) and `/sniffer/{symbol}` its per-symbol detail (see "Sniffer in
  the UI: Market Data" above). `/oink-corp` and `/warhog` are honest
  "in progress" pages.
- **`apps/api`** (FastAPI, Pydantic, SQLAlchemy 2, Alembic): the backend.
  Owns the `User` model, session issuance/verification, and the
  database-touching endpoints (`/health`, `/ready`, `/api/me`,
  `/api/system`, `/api/auth/login`, `/api/auth/logout`), plus
  `/api/market/status`, two minimal read-only Market Frame inspection
  endpoints (`/api/market/frames/latest`, `/api/market/frames/{frame_time}`),
  and three read-only Sniffer endpoints (`/api/sniffer/status`,
  `/api/sniffer/latest`, `/api/sniffer/frames/{frame_time}`). Six
  long-lived background capabilities are started from the FastAPI
  `lifespan`, not from any request: `MarketCollector`
  (`app/services/market_collector.py`, live WebSocket watching),
  `HistoryReconciler` (`app/services/history_reconciler.py`, durable
  history/bootstrap/gap repair), `OpenInterestReconciler`
  (`app/services/open_interest_reconciler.py`, OI bootstrap + poll —
  see "MARKET watches Open Interest" above), `FundingRateReconciler`
  (`app/services/funding_rate_reconciler.py`, funding bootstrap + poll —
  see "MARKET watches Funding Rate" above), `FrameSynchronizer`
  (`app/services/frame_synchronizer.py`, 5-minute cross-sectional
  synchronization), and `Sniffer` (`app/services/sniffer.py`, reactive
  per-frame feature analysis) — all six run for the life of the process.
- **`postgres`**: `users`, `instruments` (the instrument dimension, now
  also carrying `oi_synced_from`/`funding_synced_from`), `market_candles`
  (a native `RANGE`-partitioned table on `open_time`, monthly partitions,
  holding 5m/15m/1h/4h/24h — plus legacy 1m rows awaiting retention, never
  read),
  `open_interest_observations` (composite PK `(instrument_id,
  observed_at)`, deliberately *not* partitioned — see
  `OpenInterestObservation`'s model docstring for why its much smaller
  data volume doesn't need it), `funding_rate_observations` (composite PK
  `(instrument_id, funding_time)`, same not-partitioned reasoning),
  `market_frames` (one row per synchronized 5-minute boundary), `market_frame_members`
  (`RANGE`-partitioned on `frame_time`, same monthly scheme), and
  `sniffer_results` (`RANGE`-partitioned on `frame_time`, same monthly
  scheme, one row per `(frame_time, instrument_id, metric)`). No ranking
  or trade-related schema exists — those get designed when their
  requirements are known, not speculatively now.
- **`research/oink_corp`**: no longer empty — `apps/api/app/research/` +
  `app/scripts/generate_research_dataset.py` implement historical Sniff
  replay and forward-outcome research dataset generation (see "OINK CORP:
  historical Sniff replay and research datasets" above); this directory
  itself holds only its README and the gitignored `data/` output the `api`
  service bind-mounts to. `packages/shared`, `infra`: still boundaries
  reserved for future work (see each directory's own README), deliberately
  empty.
- **Vitals** (`apps/web/src/app/(protected)/vitals/page.tsx`): reads
  `/health`, `/ready`, `/api/system`, `/api/market/status`, and
  `/api/sniffer/status`. Status/health logic is a pure module
  (`apps/web/src/lib/vitals.ts`, unit tested) kept separate from the page's
  presentation, the same pattern as `lib/route-guard.ts`. The SYSTEM
  section reflects real signals (API liveness, readiness, database
  connectivity, uptime, environment, backend version); every row in MARKET
  is real too — "Bybit connectivity" from the history reconciler's own
  most recent periodic universe-refresh outcome, "Symbols tracked" from
  the persisted active-instrument count, "Symbols ready" from
  `app.services.symbol_readiness` over that same list, "Market data"/
  "Last market update"/"Data freshness" from the live collector,
  "Historical coverage" from the history reconciler's persisted progress,
  and "Latest market frame"/"Frame completeness" from the frame
  synchronizer's most recently finalized Market Frame — none of these
  issue a fresh Bybit REST call or hydrate per-member candle data on
  Vitals' behalf (see "Fix Vitals page slowness" in git history for why
  that distinction matters). In 🐽 SNIFFER,
  "Status"/"Last scan"/"Coins analyzed" are real too, reflecting
  Sniffer's own most recent analysis. Every row in 🐗 WARHOG and
  🧬 OINK CORP — including OINK CORP's "Latest ranking"/"🍄 Truffles
  found", since rankings are OINK CORP's — is hardcoded "N/A": there is no
  live signal behind any of them yet, so none is invented.

### Why no Warhog service exists yet

The spec that has driven every milestone so far is explicit: don't create
empty service scaffolding to match a future architecture. `apps/api` and
`apps/web` remain the only two applications — Sniffer is a service module
inside `apps/api` (`app/services/sniffer.py`), not a separate app or
container, and Warhog becomes real (as a module, a separate app, or
whatever its own requirements turn out to need) only when its first actual
implementation begins. Until then it's a route in the frontend that renders
an intentional "in progress" page.

## Request flow (auth)

1. Browser POSTs credentials to `/api/auth/login` (same-origin, relative
   path).
2. Next.js's catch-all route handler forwards the request to
   `apps/api`'s `/api/auth/login`, using `API_INTERNAL_URL` (resolved at
   request time, not baked into the build).
3. The API verifies the password (bcrypt via passlib), issues a signed JWT
   (`SESSION_SECRET`, `HS256`), and sets it as an `httpOnly`,
   `SameSite=Lax` cookie via `Set-Cookie`.
4. The route handler forwards `Set-Cookie` back to the browser unchanged.
5. On every subsequent request to a protected page, the relevant Next.js
   Server Component forwards the incoming `Cookie` header directly to the
   API's `/api/me` (bypassing the proxy — this is a server-to-server call)
   and redirects to `/login` if that fails.
6. Every protected API endpoint independently decodes and verifies the JWT
   via a FastAPI dependency (`app/core/deps.py::get_current_user`) — the
   frontend check is a UX convenience, not the security boundary.

## Migrations

`apps/api/docker-entrypoint.sh` runs `alembic upgrade head` on every
container start, before the server binds. With a single API replica (this
milestone's setup) there's no concurrent-migration race to worry about. See
the root [README](../README.md) for the commands to create and apply
migrations by hand.
