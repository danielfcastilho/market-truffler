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
   Sniffs, Scents, Score, Rank)
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
          🧬 OINK CORP — RESEARCH
 (feature research, backtests, Martin Gale
  simulation, calibration, walk-forward validation)
                      │
                      ▼
           develops / validates strategy
                      │
                      ▼
                   SNIFFER
              (feeds its scoring)
```

OINK CORP is where the strategy is developed and validated; Sniffer is where
that scoring runs in production, surfacing opportunities ("truffles") for
Warhog to act on. OINK CORP is not a production runtime dependency — nothing
in `docker-compose.yml` builds or starts it.

### Domain responsibilities (future)

**🐽 Sniffer — Discovery.** Exchange connectivity, historical backfills,
WebSocket market streams, normalization, storage, data quality — and,
downstream of that data, Sniffs, Scents, Score, Rank, and individual
opportunity inspection.
Discovery and analysis are one product area, not two: Sniffer's job is to
turn market data into truffles.

**🐗 Warhog — Trading.** Entries, position sizing, Martin Gale progression,
active-position management, recovery, emergency behavior, hedging, exits,
orders, fills, reconciliation.

**🧬 OINK CORP — Research.** Quantitative experiments, feature research,
backtests, Martin Gale simulation, calibration, ablation, walk-forward
validation. A repository boundary and a conceptual home for research, not a
service.

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
has no other Sniffs, Scents, Score, Rank, or Truffle qualification yet.
No Martin Gale parameters, entry/exit/hedge
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
   — the same table and partitioning serve all five timeframes.
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
   Called once per configured timeframe (five total: 1m/5m/15m/1h/4h) —
   never once per instrument. `max_open_time` is `frame_time - duration`,
   which is exactly equivalent to "close_time <= frame_time" given how M3
   already defines close_time, without needing an index on close_time at
   all.
        │
        ▼
app/repositories/frame_repository.py (FrameRepository)
   An instrument is a frame "member" only if all four *gating* timeframes
   (1m/5m/15m/1h) resolved — missing even one means no member row, never a
   fabricated placeholder. 4h is resolved the same no-look-ahead way but
   does not gate membership (see "4h: a fifth timeframe, deliberately
   non-gating" below). Batch-inserts members and flips BUILDING ->
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
forming). Verified directly against real data in this milestone's live
smoke test, and by dedicated tests in `tests/test_frame_4h.py` for the 4h
case specifically.

**Schema.** `market_frames` (frame_time as PK — no surrogate id; small,
~525,600 rows/year, not partitioned) and `market_frame_members`
(`(frame_time, instrument_id)` composite PK, partitioned by
`RANGE(frame_time)` exactly like `market_candles` — same
`app.services.partition_manager`, generalized to take a `table` parameter
rather than duplicated). A member stores five `open_time` datetimes (the
identifying half of `market_candles`' own `(instrument_id, timeframe,
open_time)` key, `timeframe` implied by the column) — never duplicated
OHLCV; `open_time_4h` is nullable (see below), the other four are not. The
dominant "give me frame T" read joins back to `market_candles` five times
(one per timeframe, the 4h join is a LEFT JOIN); "give me the latest
finalized frame" is the same, keyed off `MAX(frame_time) WHERE status !=
'building'`.

**4h: a fifth timeframe, deliberately non-gating.** `MarketFrameMember.h4`
(`app/domain/frame.py`) is `None` — never fabricated — for two honest
reasons: a member finalized before 4h tracking existed (the
`open_time_4h` column, added by migration `b53245e910f5`, is nullable
specifically so historical rows need no invented backfill value), or an
instrument whose first 4h bucket genuinely hasn't closed yet (4h buckets
take up to 4x longer to first become available than 1h). Gating
COMPLETE/PARTIAL on 4h too would make newly-listed instruments PARTIAL for
up to 4 hours for a reason unrelated to their actual live-data health, so
4h participates in frame construction (resolved, stored, joined back) but
is not part of the completeness contract; only `rsi_14_4h` (see "Sniffer
measures" below) depends on it being present, and is `None` when it isn't.

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

### Sniffer measures: `return_5m`/`return_1h` and `rsi_14_{5m,15m,1h,4h}`

Sniffer's capability is deliberately narrow: consume one finalized Market
Frame, compute factual metrics per instrument member, and persist them.
Nothing here ranks, scores, or interprets — it only measures. Today's six
Sniffs: `return_5m`, `return_1h`, `rsi_14_5m`, `rsi_14_15m`, `rsi_14_1h`,
`rsi_14_4h`. ("Sniff" is the product term for what the backend still calls
a Feature — `app/features/`, `FeatureEngine`, the `Feature` base class
below — those internal names are unchanged; only the UI-facing label is
renamed.)

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
   Return5m, Return1h, and one RsiFeature instance per timeframe (5m, 15m,
   1h, 4h) — and asks each Feature to calculate itself cross-sectionally
   over every member of the frame at once, assembling a SnifferFrameResult
   keyed by instrument.
        │
        ▼
app/features/return_5m.py and return_1h.py (both implement Feature)
   return_5m = (current_close / close_5_minutes_ago) - 1
   return_1h = (current_close / close_60_minutes_ago) - 1
   Both use Decimal. "current_close" is the frame member's own selected m1
   candle (never a fresh market_candles query — no look-ahead is even
   possible by construction). The historical anchor is member.m1.open_time minus
   exactly 5 or 60 minutes, resolved via one batched exact-open_time lookup
   (CandleRepository.fetch_exact_open_time) per distinct anchor timestamp
   across the whole frame for each feature — one query when members share
   the same anchor, never an individual query loop. Missing
   that exact candle (or a non-positive historical close) yields None —
   "unavailable" — never a substituted, zeroed, or nearest-candle value.
        │
        ▼
app/features/rsi.py (RsiFeature, one instance per timeframe)
   rsi_14_{5m,15m,1h,4h} — see "RSI features" below.
```

The hourly return uses canonical 1m candles, not the frame's closed hourly
OHLC reference. All six measurements are factual — decimal fractions or a
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
Truffles" below for how the Sniffer product surface itself is organized
around that same pipeline. A client-side column sort on the Sniffs matrix
re-orders only what's already on the page for that viewer and never calls
the backend, so it can't be mistaken for a server-side ranking.

### Sniffs → Scents → Score → Rank → Truffles

Sniffer's product surface (`apps/web/src/app/(protected)/sniffer/`) is
organized around one pipeline that both the UI and this document use
consistently:

- **Sniffs** = Feature. Factual, per-instrument measurements Sniffer has
  actually computed — facts, never judgments. Today: `return_5m`,
  `return_1h`, `rsi_14_5m`, `rsi_14_15m`, `rsi_14_1h`, `rsi_14_4h` (see
  "Sniffer measures" above). Real now, and the Sniffs matrix
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
- **🍄 Truffles** — a symbol that qualifies highly enough in the ranking
  to be surfaced as an opportunity, downstream of Rank: Sniffer's primary
  *operational* UI surface once real qualification exists, with the
  Sniffs matrix remaining the secondary research view. A Truffle is not
  synonymous with a Score or a Rank — it's the qualification decision
  built from them. Not implemented yet. The Truffle tables' `Score`
  column shows the value a symbol's rank is based on, not the rank
  position itself (that's the `#` column).

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
frame context (latest frame time, symbols analyzed), an "Inspect
symbol…" control (`SnifferSymbolSearch`) that navigates straight to
`/sniffer/{symbol}` for whatever's typed, and each direction's top 🍄
Truffles side by side (`TrufflePanel`, "Top Long Truffles"/"Top Short
Truffles"). Deliberately sparse — no charts, no KPI cards, no fabricated
activity — and has zero awareness of Warhog, trades, positions, or PnL:
Sniffer analyzes the market independently of whether anything is being
traded (see "Domain responsibilities" above — Discovery and Trading are
deliberately separate product areas). Since Truffle scoring doesn't exist
yet, both panels render the exact same truthful "No truffles yet" empty
state — never a fabricated top 5. `TrufflePanel` takes no `limit` today
(there's nothing to truncate yet) but is written generically enough to
grow past five entries once real ranking exists.

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
  Score    — Long / Short, N/A today
  Rank     — Long / Short, N/A today
  🍄 Truffle — N/A today
```

Score, Rank, and Truffle status are all "N/A" today, never `0`: `0` will
eventually be a legitimate score, so it must never be used as a stand-in
for "not computed yet". The Scents section (`SnifferScents`, reused here
rather than as a standalone route — see "Rename note" below) deliberately
shows only "No scents yet" — no Trend, Pullback, or any other dimension is
invented. The Sniffs section is built from the same `FEATURE_COLUMNS`
config the matrix uses, so its numbers can never diverge from the
matrix's. Fetches the same `getLatestSnifferFrame()` the Sniffs matrix
already uses and finds the matching instrument client-side
(case-insensitively) — no new backend endpoint. An unmatched or
not-yet-analyzed symbol renders a plain, honest inline message, never a
fabricated row or a hard crash.

No Scents, Score, Rank, or Truffles API exists either (`GET
/api/sniffer/scents/latest`, `GET /api/sniffer/scores/latest`, `GET
/api/sniffer/rank/latest`, `GET /api/sniffer/truffles/latest`, or similar
all remain unimplemented) — an honest missing endpoint rather than one
returning fake data.

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
  (`/sniffer/{symbol}`) as sibling routes (see "Sniffs → Scents → Overall
  Scent → Truffles" above) — all sharing one `sniffer/layout.tsx` shell.
- **`apps/api`** (FastAPI, Pydantic, SQLAlchemy 2, Alembic): the backend.
  Owns the `User` model, session issuance/verification, and the
  database-touching endpoints (`/health`, `/ready`, `/api/me`,
  `/api/system`, `/api/auth/login`, `/api/auth/logout`), plus
  `/api/market/status`, two minimal read-only Market Frame inspection
  endpoints (`/api/market/frames/latest`, `/api/market/frames/{frame_time}`),
  and three read-only Sniffer endpoints (`/api/sniffer/status`,
  `/api/sniffer/latest`, `/api/sniffer/frames/{frame_time}`). Four
  long-lived background capabilities are started from the FastAPI
  `lifespan`, not from any request: `MarketCollector`
  (`app/services/market_collector.py`, live WebSocket watching),
  `HistoryReconciler` (`app/services/history_reconciler.py`, durable
  history/bootstrap/gap repair), `FrameSynchronizer`
  (`app/services/frame_synchronizer.py`, per-minute cross-sectional
  synchronization), and `Sniffer` (`app/services/sniffer.py`, reactive
  per-frame feature analysis) — all four run for the life of the process.
- **`postgres`**: `users`, `instruments` (the instrument dimension),
  `market_candles` (a native `RANGE`-partitioned table on `open_time`,
  monthly partitions, holding all five timeframes: 1m/5m/15m/1h/4h),
  `market_frames` (one row per synchronized minute), `market_frame_members`
  (`RANGE`-partitioned on `frame_time`, same monthly scheme), and
  `sniffer_results` (`RANGE`-partitioned on `frame_time`, same monthly
  scheme, one row per `(frame_time, instrument_id, metric)`). No ranking or
  trade-related schema exists — those get designed when their requirements
  are known, not speculatively now.
- **`research/oink_corp`, `packages/shared`, `infra`**: boundaries reserved
  for future work (see each directory's own README). Deliberately empty.
- **Vitals** (`apps/web/src/app/(protected)/vitals/page.tsx`): reads
  `/health`, `/ready`, `/api/system`, `/api/market/status`, and
  `/api/sniffer/status`. Status/health logic is a pure module
  (`apps/web/src/lib/vitals.ts`, unit tested) kept separate from the page's
  presentation, the same pattern as `lib/route-guard.ts`. The SYSTEM
  section reflects real signals (API liveness, readiness, database
  connectivity, uptime, environment, backend version); every row in MARKET
  is real too — "Bybit connectivity"/"Symbols tracked" from a REST call,
  "Market data"/"Last market update"/"Data freshness" from the live
  collector, "Historical coverage" from the history reconciler's persisted
  progress, and "Latest market frame"/"Frame completeness" from the frame
  synchronizer's most recently finalized Market Frame. In 🐽 SNIFFER,
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
