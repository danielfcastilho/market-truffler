# Architecture

This document describes two things: the **future conceptual system** Market
Truffler is being built toward, and the **actual state** of this foundation
milestone. Don't confuse the two — most of what's below is not implemented
yet, by design.

## The future system

Market Truffler has three product areas in its trading pipeline, plus one
cross-cutting operational area that observes all of them. Sniffer owns
everything between raw market data and a ranked opportunity — including
analysis, scoring, and individual coin/opportunity inspection, which are not
a separate product area:

```
                    BYBIT
                      │
                      ▼
            🐽 SNIFFER — DISCOVERY
  (exchange connectivity, market-data acquisition,
   Sniffs, Scents, Score, Rank, Qualification)
                      │
                      ▼
           🍄 opportunities / truffles
                      │
                      ▼
             🐗 WARHOG — TRADING
 (entries, position sizing, Martin Gale,
  active-position management, hedging, exits)
```

Separately, alongside but outside that runtime pipeline:

```
     MARKET (historical truth)
                      │
                      ▼
          🧬 OINK CORP — RESEARCH
 (feature research, backtests, Martin Gale
  simulation, calibration, walk-forward validation)
                      │
                      │ develops, validates, promotes
                      ▼
             SCENT MODEL vN
                      │
                      ▼
                   SNIFFER
     (Scents → Score → Rank → Qualification → Truffles)
```

OINK CORP is where the strategy is developed and validated; Sniffer is where
that scoring runs in production, surfacing opportunities ("truffles") for
Warhog to act on. OINK CORP is not a production runtime dependency — nothing
in `docker-compose.yml` builds or starts it, and production Sniffer never
depends on OINK CORP being live: it executes whatever Scent Model was last
deliberately promoted, not whatever OINK CORP happens to be experimenting
with right now. See "OINK CORP and the Scent Model" below for the versioned
contract between the two.

### Domain responsibilities (future)

**🐽 Sniffer — Discovery.** Exchange connectivity, historical backfills,
WebSocket market streams, normalization, storage, data quality — and,
downstream of that data, Sniffs, Scents, Score, Rank, Qualification, and
individual opportunity inspection.
Discovery and analysis are one product area, not two: Sniffer's job is to
turn market data into truffles. Sniffer is a read-only production
*interpretation* surface — it explains Sniff/Scent/Score/Rank/Qualification
values, but is never where a user casually changes a strategy parameter
(see "OINK CORP has knobs; Sniffer has gauges" below).

**🐗 Warhog — Trading.** Entries, position sizing, Martin Gale progression,
active-position management, recovery, emergency behavior, hedging, exits,
orders, fills, reconciliation.

**🧬 OINK CORP — Research.** Quantitative experiments, feature research,
backtests, Martin Gale simulation, calibration, ablation, walk-forward
validation. A repository boundary and a conceptual home for research, not a
service — it has the knobs (parameters, thresholds, candidate formulas)
that Sniffer deliberately does not expose. See "OINK CORP and the Scent
Model" below for how (and when) its output actually reaches production.

**🩺 Vitals — System Health.** Not part of the trading pipeline — it sits
outside it and observes all of it. Answers one question: "is Market Truffler
operating correctly?" Vitals monitors real runtime components and
dependencies — it never fabricates a status for one that doesn't exist yet.
Its layout previews the areas it will eventually report on (Market,
Sniffer, Warhog, OINK CORP), but until each is real, every row in those
sections is an honest "N/A," not a simulated value. Unlike the other three
areas, Vitals is partially implemented today — see below.

Sniffer's factual measurements — `return_5m`, `return_1h`, `rsi_14_5m`,
`rsi_14_15m`, `rsi_14_1h`, `rsi_14_4h`, six metrics per instrument per
Market Frame — are implemented (see "Sniffer measures" below). Beyond
that, Warhog and OINK CORP remain fully unimplemented, and Sniffer itself
has no other Sniffs, Scents, Score, Rank, or Qualification yet — so no
Truffles exist either. No Martin Gale parameters, entry/exit/hedge
rules, or live execution architecture have been decided either. Those are
later engineering/research milestones, and this codebase makes no
assumptions about them.

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

### Live MARKET watching: the WebSocket collector

Beyond REST discovery, MARKET now continuously watches the discovered
universe over Bybit's public WebSocket and turns confirmed klines into
canonical closed 1-minute candles:

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
5m/15m/1h/4h — all as a continuously-running background capability, never
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
   — the same table and partitioning serve all six timeframes.
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
   The shared canonical-1m boundary both the live sink and the reconciler
   funnel through: upsert the 1m batch, then re-derive any 5m/15m/1h/4h
   window the batch touches — but only ones where every constituent 1m
   candle actually exists in storage. An incomplete window is silently
   left alone and completes itself whenever this is next called over a
   range that includes it (a later recovery, or a correction). 4h is
   derived the same one-level-from-1m way as every other timeframe here
   (240 constituent 1m candles, not chained through 1h) — no special-casing
   per timeframe beyond one more entry in `_DERIVED_TIMEFRAMES`.
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
(`Return5m`/`Return1h`/`ReturnFeature` — their lookback constant;
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
gate on `READY` directly (see "Sniffs → Scents → Score → Rank →
Qualification → Truffles" below) — this is the hook a future one uses,
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
   equivalent of 1m candles. Bybit's public WebSocket does carry a live
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
knowable about the whole market at time T?" Every closed UTC minute, a
third background capability turns `market_candles` (many independent
per-instrument, per-timeframe time series) into one synchronized
cross-sectional snapshot future Sniffer can consume without re-deriving
temporal alignment itself:

```
app/services/frame_synchronizer.py (FrameSynchronizer)
   Wakes exactly on UTC minute boundaries. At frame_time = M: snapshots the
   *active* universe right then (fixing expected_instruments immutably —
   a later universe change never rewrites an already-finalized frame's
   expectations), persists a BUILDING row immediately (crash-safe), waits
   a short configurable grace period for normal WebSocket delivery jitter
   to settle, then finalizes — never on the first symbol to arrive.
        │
        ▼
app/repositories/candle_repository.py
   fetch_latest_closed_per_instrument(timeframe, instrument_ids, max_open_time)
   One set-oriented query (a portable ROW_NUMBER() OVER (PARTITION BY
   instrument_id ORDER BY open_time DESC) window function — works
   identically on SQLite in tests and PostgreSQL in production) resolves
   the whole active universe's latest *legal* candle for one timeframe.
   Called once per configured timeframe (six total: 1m/5m/15m/1h/4h/24h) —
   never once per instrument. `max_open_time` is `frame_time - duration`,
   which is exactly equivalent to "close_time <= frame_time" given how M3
   already defines close_time, without needing an index on close_time at
   all.
        │
        ▼
app/repositories/frame_repository.py (FrameRepository)
   An instrument is a frame "member" only if all four *gating* timeframes
   (1m/5m/15m/1h) resolved — missing even one means no member row, never a
   fabricated placeholder. 4h and 24h are resolved the same no-look-ahead
   way but do not gate membership (see "4h/24h: two non-gating timeframes"
   below). Batch-inserts members and flips BUILDING ->
   COMPLETE (available == expected) or PARTIAL (available < expected),
   guarded so a second finalize attempt (a duplicate trigger, or
   late-arriving recovered data) can never rewrite an already-terminal
   frame — a finalized frame is an immutable statement of what MARKET
   actually had available at T.
```

**Frame time.** `frame_time = M` means "the decision point immediately
after the M-1..M minute closed." A frame may only reference candles with
`close_time <= frame_time` — e.g. at `frame_time=14:37`, the legal 1h
candle is `13:00-13:59` (`14:00-14:59` is still forming, so it can never be
selected); the legal 4h candle is `08:00-11:59` (`12:00-15:59` is still
forming); the legal 24h candle (on that same day) is the *previous* day's
`00:00-23:59` UTC bucket, since today's own hasn't closed yet. Verified
directly against real data in this milestone's live smoke test, and by
dedicated tests in `tests/test_frame_4h.py`/`tests/test_frame_24h.py` for
the 4h/24h cases specifically.

**Schema.** `market_frames` (frame_time as PK — no surrogate id; small,
~525,600 rows/year, not partitioned) and `market_frame_members`
(`(frame_time, instrument_id)` composite PK, partitioned by
`RANGE(frame_time)` exactly like `market_candles` — same
`app.services.partition_manager`, generalized to take a `table` parameter
rather than duplicated). A member stores six `open_time` datetimes (the
identifying half of `market_candles`' own `(instrument_id, timeframe,
open_time)` key, `timeframe` implied by the column) — never duplicated
OHLCV; `open_time_4h`/`open_time_24h` are nullable (see below), the other
four are not. The dominant "give me frame T" read joins back to
`market_candles` six times (one per timeframe, the 4h/24h joins are LEFT
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
`frame_time`, not on when the query runs) and resumes the per-minute loop
from there; it never fabricates frames for whatever minutes were missed
while the process was down.

Vitals reads two new values — "Latest market frame" and "Frame
completeness" — purely from `FrameRepository.get_latest_finalized()`;
opening Vitals never creates, advances, or otherwise drives a frame.

### Sniffer measures: Returns, RSI, Open Interest change, and Volatility

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
   Return5m, Return1h (the original two, kept as their own dedicated,
   already-tested classes), ReturnFeature("15m"/"4h"/"24h") (a later,
   generalized "one class, many timeframe instances" class — the same
   pattern RsiFeature already established), one RsiFeature instance per
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
app/features/return_5m.py, return_1h.py, and return_feature.py (all implement Feature)
   return_{timeframe} = (current_close / close_{timeframe}_ago) - 1
   Uses Decimal. "current_close" is the frame member's own selected m1
   candle (never a fresh market_candles query — no look-ahead is even
   possible by construction). The historical anchor is
   member.m1.open_time minus the labeled lookback, resolved via one
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

The hourly return uses canonical 1m candles, not the frame's closed hourly
OHLC reference. All measurements are factual — decimal fractions or a
plain 0-100 index, never desirability or trade signals. The API and
Sniffer's Sniffs view expose all of them; older stored analyses without a
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
`market_candles`' 1m→5m/15m/1h/4h derivation uses, just without needing
any local aggregation step (Bybit already provides the finest granularity
this app needs, unlike candles' 1m).

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
of what the UI does with them; see "Sniffs → Scents → Score → Rank →
Qualification → Truffles" below for how the Sniffer product surface itself
is organized around that same pipeline. A client-side column sort on the
Sniffs matrix re-orders only what's already on the page for that viewer and
never calls the backend, so it can't be mistaken for a server-side ranking.

### Sniffs → Scents → Score → Rank → Qualification → Truffles

Sniffer's product surface (`apps/web/src/app/(protected)/sniffer/`) is
organized around one pipeline that both the UI and this document use
consistently:

- **Sniffs** = Feature. Factual, per-instrument measurements Sniffer has
  actually computed — facts, never judgments. Today: Returns (`return_5m`,
  `return_15m`, `return_1h`, `return_4h`, `return_24h`), RSI(14)
  (`rsi_14_5m`, `rsi_14_15m`, `rsi_14_1h`, `rsi_14_4h`), Open Interest
  change (`oi_change_5m`, `oi_change_15m`, `oi_change_1h`, `oi_change_4h`,
  `oi_change_24h`), Volatility/ATR% (`volatility_15m`,
  `volatility_1h`, `volatility_4h`, `volatility_24h`), Relative Volume
  (`relative_volume_15m`, `relative_volume_1h`, `relative_volume_4h`),
  and Funding Rate (`funding_rate_current`, `funding_rate_24h_avg`) — see
  "Sniffer measures" above. Real now, and the Sniffs matrix
  (`SnifferSniffs`/`SnifferTable`) is a secondary inspection/research
  surface — it answers "what does Sniffer currently know about each
  coin?", nothing more. "Sniff" is purely the UI/product term; the
  backend still calls this concept a Feature (`app/features/`,
  `FeatureEngine`, `SnifferInstrument`'s field keys like `rsi_14_5m`) —
  those internal names are deliberately unchanged: "Sniff" is only how a
  Feature is presented to the user, not a reason to rename stable backend
  architecture.
- **Scents** = Pillar. Future interpreted quantitative dimensions built
  from one or more Sniffs (e.g. a future Trend Scent, Pullback Scent —
  what a "Pillar" was called in earlier design language, then briefly
  "Scent Notes"; both are retired in favor of "Scents"). A Scent is
  interpretation, not a raw measurement — it answers "how does Sniffer
  read this dimension of the market?" Not implemented, and none are
  defined yet — no Trend, Pullback, Momentum, Stability, Activity,
  Liquidity, Volatility, or any other dimension exists in the code, only
  the concept of the layer. Scents describe one symbol, not the whole
  universe, so they have no standalone global page — they belong on
  `/sniffer/{symbol}` (see below), the explanation of how a symbol
  eventually gets its Score.
- **Score** (Long Score / Short Score) — the final per-direction
  directional desirability for a symbol, aggregated from its Scents,
  backed internally by `long_score`/`short_score`, each independently in
  `[0, 1]`. Not implemented. Deliberately *not* called "Long/Short Scent"
  — "Scent" is reserved for the intermediate Pillar-level dimensions a
  Score is built from, not the aggregate itself, so the two layers stay
  distinguishable in conversation and in the UI. Long and Short Score are
  not two ends of one scale — a coin can score high on both, low on both,
  or anywhere in between, so nothing in the data model or UI may assume
  they're mutually exclusive.
- **Rank** (Long Rank / Short Rank) — a symbol's position relative to the
  rest of the analyzed universe for that direction (Long ordered by
  `long_score` descending, Short by `short_score` descending). Not
  implemented. Previously documented as a purely internal ordering
  mechanism with no UI surface of its own; now a first-class step shown
  on `/sniffer/{symbol}` alongside Score, since "how does this symbol
  compare to the rest of the universe" is a genuinely different, useful
  fact from "what is this symbol's raw Score" — see "Rename note" below.
- **Qualification** — the classification step that decides whether one
  direction's Score/Rank becomes a Truffle. Not a metric of its own and
  not exposed as a value anywhere in the UI; it's a rule applied to Score
  and Rank, producing a yes/no per direction. The settled V1 conceptual
  rule requires **both** an absolute-quality gate and a relative-scarcity
  gate:

  ```
  qualifies = Score >= minimum_score_threshold AND Rank <= 10
  ```

  applied independently per direction (`Green Truffle` from the Long side,
  `Red Truffle` from the Short side — see below). The actual
  `minimum_score_threshold` value is deliberately undecided — this
  document does not invent one, and none is hardcoded anywhere in the
  code. Top 10 is a **cap**, not a fill target: 0 symbols clearing the
  threshold means 0 Truffles that direction, 25 clearing it means only the
  best-ranked 10 become Truffles, and Sniffer must never lower the
  threshold or otherwise fabricate opportunities just to fill ten slots.
  Not implemented yet — see "OINK CORP and the Scent Model" below for
  where such a threshold would eventually be decided and by what process.
- **🍄 Truffles** — a symbol/direction that has passed Qualification.
  Sniffer's primary *operational* UI surface once real qualification
  exists, with the Sniffs matrix remaining the secondary research view. A
  Truffle is not synonymous with a Score or a Rank, nor merely "the #1
  symbol" or "every member of a fixed Top N" — it's the qualification
  decision built from them, and a symbol can independently end up
  Green-qualified, Red-qualified, both, or neither. Not implemented yet.
  The Truffle tables' `Score` column shows the value a symbol's rank is
  based on, not the rank position itself (that's the `#` column, i.e.
  Rank).

**Routing.** Three routes under `apps/web/src/app/(protected)/sniffer/`,
one shared shell (`layout.tsx`: the `🐽 Sniffer` title plus `SnifferNav`
— a client component that derives its own active tab from
`usePathname()`, no query-param state left anywhere in Sniffer):

```
/sniffer             🍄 Truffles dashboard (page.tsx itself —
                      Sniffer's primary page; SnifferDashboard)
/sniffer/sniffs       Sniffs matrix (SnifferSniffs/SnifferTable)
/sniffer/{symbol}     Symbol detail (SnifferSymbolDetail) — dynamic,
                      resolves after the two static routes above
```

**🍄 Truffles dashboard (`/sniffer`).** Sniffer's primary page — there is
no separate "Dashboard" view; Truffles *are* the dashboard's main content,
not a different product, and the nav's "🍄 Truffles" tab points at and is
active on `/sniffer` itself. Answers "what is Sniffer seeing right now?":
frame context (latest frame time, symbols analyzed) and an "Inspect
symbol…" control (`SnifferSymbolSearch`, navigates straight to
`/sniffer/{symbol}` for whatever's typed) sit above a secondary
**Green Truffles / Red Truffles** tab control (`TruffleDirectionTabs`) —
only one directional ranking table is shown at a time, so it gets the
full content width instead of splitting it with a sibling table. Green is
the default tab; that's a UI default only, never a claim that Long is
mathematically preferred. The `#` / `Symbol` / `Score` ranking table
(`TrufflePanel`) itself carries no heading of its own — the active tab's
icon + "Green Truffles"/"Red Truffles" text already identifies the
direction. The dashboard is deliberately sparse — no charts, no KPI cards,
no fabricated activity — and has zero awareness of Warhog, trades,
positions, or PnL: Sniffer analyzes the market independently of whether
anything is being traded (see "Domain responsibilities" above — Discovery
and Trading are deliberately separate product areas). Since Score/Rank/
Qualification don't exist yet, both directions render the exact same
truthful "No truffles yet" empty state — never a fabricated ranking. Once
qualification exists, each direction's table can hold anywhere from 0 to
10 rows (Qualification's Top-10 cap — see above), never padded or
paginated to look fuller than it is.

The custom `TruffleIcon` component (`truffle-icon.tsx`) is the one visual
source of truth for the green/red directional mushroom used both by these
tabs and by the qualification badge on `/sniffer/{symbol}` (see below) —
both directions render the identical silhouette; only the cap's fill color
differs.

*(`/sniffer/truffles` briefly existed as a separate route before Dashboard
and Truffles were recognized as the same thing and merged; it now
redirects to `/sniffer` via `next.config.ts`'s `redirects()` rather than
keeping a second page implementation.)*

**Sniffs (`/sniffer/sniffs`).** Unchanged — the full analytical matrix
over the whole tracked universe (see `sniffer-table.tsx` above). Every
Symbol cell links to `/sniffer/{symbol}`; that link is independent of
sorting/filtering, which both still operate on the plain symbol string.

**Symbol detail (`/sniffer/{symbol}`).** The consolidated microscope for
one symbol, laid out in the same order the pipeline flows — facts first,
conclusions last:

```
SYMBOL
  Sniffs   — factual measurements, grouped like the matrix (Returns, RSI)
  Scents   — the interpreted dimensions that would explain the Score
  Score    — Long / Short, N/A today (a qualified direction's Truffle
             badge, once Qualification exists, renders here — see below)
  Rank     — Long / Short, N/A today
```

There is deliberately **no standalone "Truffles" section** here. A Truffle
is a classification of a directional Score, not another numeric stage
alongside Score/Rank — so it has no row or metric of its own. Instead,
`StatRow` (the shared label/value row both Score and Rank are built from)
accepts an optional `qualification` prop; when a direction's Score has
qualified, that row renders `TruffleQualificationBadge` — the same
`TruffleIcon` used on the dashboard tabs — immediately beside the Score
value (never duplicated beside Rank), e.g. conceptually:

```
Score
  Long      0.91   🟢   ← qualified: Green Truffle badge beside Long Score
  Short     0.34

Rank
  Long        #3
  Short      #412
```

The badge's placement beside Score is a **presentation** choice (the
cleanest way to show "this directional opportunity qualified"), not a
claim about computational order — Qualification still conceptually runs
*after* both Score and Rank exist (see "Qualification" above), it's just
rendered next to Score. Today neither Score row passes a `qualification`
value (no qualification rule exists yet), so no badge renders at all —
never a greyed-out/disabled mushroom, never a "Truffle: N/A" placeholder.
Absence of the badge is the correct representation while Qualification is
unimplemented.

Score and Rank are "N/A" today, never `0`: `0` will eventually be a
legitimate score, so it must never be used as a stand-in for "not computed
yet". The Scents section (`SnifferScents`, reused here rather than as a
standalone route — see "Rename note" below) deliberately shows only "No
scents yet" — no Trend, Pullback, or any other dimension is invented. The
Sniffs section is built from the same `FEATURE_COLUMNS` config the matrix
uses, so its numbers can never diverge from the matrix's. Fetches the same
`getLatestSnifferFrame()` the Sniffs matrix already uses and finds the
matching instrument client-side (case-insensitively) — no new backend
endpoint. An unmatched or not-yet-analyzed symbol renders a plain, honest
inline message, never a fabricated row or a hard crash.

No Scents, Score, Rank, Qualification, or Truffles API exists either (`GET
/api/sniffer/scents/latest`, `GET /api/sniffer/scores/latest`, `GET
/api/sniffer/rank/latest`, `GET /api/sniffer/truffles/latest`, or similar
all remain unimplemented) — an honest missing endpoint rather than one
returning fake data. No placeholder endpoint has been added to make this
architecture look more implemented than it is.

**Rename note.** The Sniffer UI nav was originally called "Rankings"; it is
now "🍄 Truffles" since Ranking is a mechanism, while Truffles — the
qualified opportunities ranking eventually produces — is what the UI
actually shows the user. The design-language term "Pillars" went through
"Scent Notes" before settling on "Scents" for the interpreted-dimension
layer built from Sniffs. The UI/product term for Sniffer's factual
measurements is "Sniffs" (`/sniffer/sniffs`, previously
`/sniffer/features`, itself previously `?view=features`) — this is a
UI-only rename: the backend's `Feature`/`FeatureEngine`/`app/features/`
and every metric key (`return_5m`, `rsi_14_5m`, ...) are deliberately
unchanged, since "Sniff" is only how a Feature is presented to the user,
not a reason to rename stable backend architecture.

Most recently, a nomenclature audit found the UI had drifted from its own
backend naming: the symbol page showed "Long Scent"/"Short Scent" for the
final per-direction aggregate, and the Truffle tables labeled that same
value's column "Scent" — but `long_score`/`short_score` had *always* been
the intended internal name for that concept, and "Score" is what those
names actually say. The UI, not the backend, was the inconsistent side;
the fix renamed the UI labels to "Long/Short **Score**" rather than
touching the (already-correct) internal names. "Scent" is now reserved
exclusively for the intermediate Pillar-level dimensions (Trend, Pullback,
...) that a Score is built from, so the two layers stay distinguishable.
Ranking's status was also promoted: it used to be documented as a purely
internal mechanism with no UI surface of its own; it's now **Rank**
(Long Rank / Short Rank), a first-class concept shown on
`/sniffer/{symbol}` next to Score, since "how does this symbol compare to
the rest of the universe" is a genuinely different fact worth showing
from "what is this symbol's raw Score". Two aspirational, never-implemented
terms — "desirability functions" and "Entry Fitness" — were retired from
the high-level diagrams above in favor of the concrete `Score`/`Rank`
vocabulary now that the full pipeline is named end to end.

`/sniffer` itself briefly became a separate "Dashboard" (distinct from a
`/sniffer/truffles` route) before Dashboard and Truffles were recognized
as the same thing and merged back into one page at `/sniffer` — there is
no "Dashboard" nav item; "🍄 Truffles" is `/sniffer`. Scents was removed
as a standalone top-level tab/route (previously `?view=scents`) — Scents
describes one symbol's interpreted dimensions, so it lives on
`/sniffer/{symbol}` instead of an empty global matrix with nothing yet to
show.

Two more recent corrections: the dashboard's two rankings were originally
shown side by side, headed "Top Long Truffles"/"Top Short Truffles"; they
are now **Green Truffles / Red Truffles** secondary tabs (only one
ranking visible at a time — see "🍄 Truffles dashboard" above), since
Green/Red is the settled directional-Truffle visual language and
"Top Long/Short Truffles" both repeated "Truffles" as if it were the
metric name and mixed Long/Short (Score/Rank's technical direction)
with the product-facing Truffle vocabulary. And the symbol page
originally had a standalone `🍄 Truffle — N/A` section (later briefly
`Green Truffle`/`Red Truffle` N/A rows) after Rank, treating Truffle
qualification as a fourth metric alongside Sniffs/Scents/Score/Rank; it
was removed once "a Truffle is a classification of a Score, not another
metric" was settled — see "Symbol detail" above for the current
badge-beside-Score representation.

### OINK CORP and the Scent Model

OINK CORP and Sniffer are deliberately decoupled by a versioned contract,
not a live dependency:

```
notebooks / experiments
         ↓
   candidate model
         ↓
    validation
         ↓
     PROMOTE
         ↓
  Scent Model v1
         ↓
     Sniffer
```

OINK CORP experiments freely — nothing about production Sniffer changes
merely because an experiment exists in `research/oink_corp`. A candidate
mathematical definition must be deliberately **promoted** to an active
versioned Scent Model before Sniffer ever executes it. A future example of
what that might look like (not implemented, no registry/schema/promotion
workflow exists yet):

```
Scent Model v1   ACTIVE
Scent Model v2   EXPERIMENT
Scent Model v3   REJECTED
```

The invariant this protects: if a number affects whether something
becomes a Truffle, its mathematical definition should eventually be
traceable to the active versioned Scent Model that OINK CORP produced and
validated — never an ad hoc formula that only lives in Sniffer's code with
no research trail behind it.

**Questions OINK CORP is where to investigate** (not answered by this
document, and explicitly out of scope for this milestone): which Scents
should exist; which Sniffs feed each one; how a raw Sniff gets interpreted
into a desirability judgment; whether that interpretation is a simple
weighted function or something nonlinear/multivariable; how Scents combine
into Score; whether Long and Short should start symmetric; what minimum
Score threshold should qualify a Truffle (see "Qualification" above — this
is exactly the undecided `minimum_score_threshold`); whether Top 10 is the
right scarcity cap compared to alternatives; and whether a candidate model
actually survives historical/out-of-sample/walk-forward validation.

**OINK CORP has knobs; Sniffer has gauges.** Sniffer is a read-only
production interpretation surface: it can eventually explain a Sniff
value, a Scent value, Long/Short Score, Long/Short Rank, and Truffle
Qualification, but it is never where a user casually adjusts a strategy
parameter. Research/calibration controls belong conceptually to OINK
CORP, not to any UI shipped under `/sniffer`.

**Explainability is mandatory; weights are optional.** Every
Sniff → Scent → Score transformation should eventually expose enough
information to explain how its output was obtained. For a simple weighted
model that might look like `raw Sniff → interpretation function →
interpreted desirability → Influence → contribution` ("Influence" is the
preferred product/UI word once a true mathematical weight exists — never
assume every model has one). A future Scent may instead be nonlinear or
multivariable (e.g. `PullbackLong = f(rsi_5m, rsi_15m, return_5m, ...)`),
in which case there may be no mathematically honest per-feature
weight/contribution to show — explainability must never be fabricated
just to keep the UI uniform across Scents that don't share a common
mathematical shape.

**Interpretation, not a generic penalty subsystem.** Raw Sniffs are
factual measurements and must not themselves encode strategy judgment
(e.g. `rsi_5m = 31` is a fact, not a verdict). A future model applies its
own interpretation function (`f_pullback_long_rsi5m(31) → interpreted
desirability`), which can naturally represent attractive/neutral/
undesirable regions on its own — a separate generic "Penalty" mechanism
should exist only if future research actually justifies one, not merely
because some raw Sniff values happen to look unfavorable.

**Future research organization** (illustrative only — none of this exists
yet, and no empty notebooks should be created just to match the diagram):

```
research/oink_corp/
    notebooks/
        sniffs/
            return_5m.ipynb
            rsi_14_5m.ipynb
        scents/
            trend_long.ipynb
            pullback_long.ipynb
        scores/
            long_score.ipynb
            short_score.ipynb
        qualification/
            truffle_qualification.ipynb
```

Notebooks are research/specification artifacts, never production runtime.
Whatever mathematics is eventually promoted should live in normal, tested
production code shared appropriately with research, so notebook math can
never silently drift from what Sniffer actually executes.

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
  origin. `/sniffer` is the 🍄 Truffles dashboard (Sniffer's primary
  page), with the Sniffs matrix (`/sniffer/sniffs`) and per-symbol detail
  (`/sniffer/{symbol}`) as sibling routes (see "Sniffs → Scents → Score →
  Rank → Qualification → Truffles" above) — all sharing one
  `sniffer/layout.tsx` shell.
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
  (`app/services/frame_synchronizer.py`, per-minute cross-sectional
  synchronization), and `Sniffer` (`app/services/sniffer.py`, reactive
  per-frame feature analysis) — all six run for the life of the process.
- **`postgres`**: `users`, `instruments` (the instrument dimension, now
  also carrying `oi_synced_from`/`funding_synced_from`), `market_candles`
  (a native `RANGE`-partitioned table on `open_time`, monthly partitions,
  holding all six timeframes: 1m/5m/15m/1h/4h/24h),
  `open_interest_observations` (composite PK `(instrument_id,
  observed_at)`, deliberately *not* partitioned — see
  `OpenInterestObservation`'s model docstring for why its much smaller
  data volume doesn't need it), `funding_rate_observations` (composite PK
  `(instrument_id, funding_time)`, same not-partitioned reasoning),
  `market_frames` (one row per synchronized minute), `market_frame_members`
  (`RANGE`-partitioned on `frame_time`, same monthly scheme), and
  `sniffer_results` (`RANGE`-partitioned on `frame_time`, same monthly
  scheme, one row per `(frame_time, instrument_id, metric)`). No ranking
  or trade-related schema exists — those get designed when their
  requirements are known, not speculatively now.
- **`research/oink_corp`, `packages/shared`, `infra`**: boundaries reserved
  for future work (see each directory's own README). Deliberately empty.
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
  "Status"/"Last scan"/"Coins analyzed" are now real too, reflecting
  Sniffer's own most recent analysis; "Latest ranking" and
  "🍄 Truffles found" stay hardcoded to "N/A", and all of 🐗 WARHOG and
  🧬 OINK CORP still do too: there is no live signal behind any of them
  yet, so none is invented.

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
