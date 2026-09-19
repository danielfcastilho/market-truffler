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
   features, desirability functions, pillars,
   Entry Fitness, Long/Short rankings)
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
downstream of that data, features, desirability functions, pillars, Entry
Fitness, Long ranking, Short ranking, and individual opportunity inspection.
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

None of Sniffer, Warhog, or OINK CORP is implemented in this milestone. No
indicators, features, pillars, desirability functions, weights, Entry
Fitness, rankings, Martin Gale parameters, entry/exit/hedge rules, or live
execution architecture have been decided. Those are later
engineering/research milestones, and this codebase makes no assumptions
about them.

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
~1-year horizon, self-repairing after gaps or outages, and locally
aggregated into 5m/15m/1h — all as a continuously-running background
capability, never triggered by a request:

```
app/models/instrument.py, app/models/candle.py
   Instrument: one row per (exchange, symbol) MARKET has ever tracked,
   never deleted, carrying the three-watermark bootstrap/reconciliation
   state (history_target_start/synced_from/synced_through — see the
   model's docstring for exact semantics).
   Candle: composite PK (instrument_id, timeframe, open_time), a native
   PostgreSQL table partitioned by RANGE(open_time) in monthly partitions
   — the same table and partitioning serve all four timeframes.
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
   history_synced_from backward toward history_target_start (bootstrap)
   and one walks history_synced_through forward toward "now minus a small
   buffer" (live catch-up/gap-repair) — the same REST kline endpoint,
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
   funnel through: upsert the 1m batch, then re-derive any 5m/15m/1h window
   the batch touches — but only ones where every constituent 1m candle
   actually exists in storage. An incomplete window is silently left alone
   and completes itself whenever this is next called over a range that
   includes it (a later recovery, or a correction).
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
   Called once per configured timeframe (four total) — never once per
   instrument. `max_open_time` is `frame_time - duration`, which is exactly
   equivalent to "close_time <= frame_time" given how M3 already defines
   close_time, without needing an index on close_time at all.
        │
        ▼
app/repositories/frame_repository.py (FrameRepository)
   An instrument is a frame "member" only if all four configured
   timeframes resolved — missing even one means no member row, never a
   fabricated placeholder. Batch-inserts members and flips
   BUILDING -> COMPLETE (available == expected) or PARTIAL (available <
   expected), guarded so a second finalize attempt (a duplicate trigger,
   or late-arriving recovered data) can never rewrite an already-terminal
   frame — a finalized frame is an immutable statement of what MARKET
   actually had available at T.
```

**Frame time.** `frame_time = M` means "the decision point immediately
after the M-1..M minute closed." A frame may only reference candles with
`close_time <= frame_time` — e.g. at `frame_time=14:37`, the legal 1h
candle is `13:00-13:59` (`14:00-14:59` is still forming); verified directly
against real data in this milestone's live smoke test.

**Schema.** `market_frames` (frame_time as PK — no surrogate id; small,
~525,600 rows/year, not partitioned) and `market_frame_members`
(`(frame_time, instrument_id)` composite PK, partitioned by
`RANGE(frame_time)` exactly like `market_candles` — same
`app.services.partition_manager`, generalized to take a `table` parameter
rather than duplicated). A member stores only four `open_time` datetimes
(the identifying half of `market_candles`' own
`(instrument_id, timeframe, open_time)` key, `timeframe` implied by the
column) — never duplicated OHLCV. The dominant "give me frame T" read
joins back to `market_candles` four times (one per timeframe); "give me the
latest finalized frame" is the same, keyed off `MAX(frame_time) WHERE
status != 'building'`.

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
  origin.
- **`apps/api`** (FastAPI, Pydantic, SQLAlchemy 2, Alembic): the backend.
  Owns the `User` model, session issuance/verification, and the
  database-touching endpoints (`/health`, `/ready`, `/api/me`,
  `/api/system`, `/api/auth/login`, `/api/auth/logout`), plus
  `/api/market/status` and two minimal read-only inspection endpoints
  (`/api/market/frames/latest`, `/api/market/frames/{frame_time}`). Three
  long-lived background capabilities are started from the FastAPI
  `lifespan`, not from any request: `MarketCollector`
  (`app/services/market_collector.py`, live WebSocket watching),
  `HistoryReconciler` (`app/services/history_reconciler.py`, durable
  history/bootstrap/gap repair), and `FrameSynchronizer`
  (`app/services/frame_synchronizer.py`, per-minute cross-sectional
  synchronization) — all three run for the life of the process.
- **`postgres`**: `users`, `instruments` (the instrument dimension),
  `market_candles` (a native `RANGE`-partitioned table on `open_time`,
  monthly partitions, holding all four timeframes), `market_frames` (one
  row per synchronized minute), and `market_frame_members`
  (`RANGE`-partitioned on `frame_time`, same monthly scheme). No ranking,
  feature, or trade-related schema exists — those get designed when their
  requirements are known, not speculatively now.
- **`research/oink_corp`, `packages/shared`, `infra`**: boundaries reserved
  for future work (see each directory's own README). Deliberately empty.
- **Vitals** (`apps/web/src/app/(protected)/vitals/page.tsx`): reads
  `/health`, `/ready`, `/api/system`, and `/api/market/status`.
  Status/health logic is a pure module (`apps/web/src/lib/vitals.ts`, unit
  tested) kept separate from the page's presentation, the same pattern as
  `lib/route-guard.ts`. The SYSTEM section reflects real signals (API
  liveness, readiness, database connectivity, uptime, environment, backend
  version); every row in MARKET is real too — "Bybit
  connectivity"/"Symbols tracked" from a REST call, "Market
  data"/"Last market update"/"Data freshness" from the live collector,
  "Historical coverage" from the history reconciler's persisted progress,
  and "Latest market frame"/"Frame completeness" from the frame
  synchronizer's most recently finalized Market Frame. 🐽 SNIFFER,
  🐗 WARHOG, and 🧬 OINK CORP stay hardcoded to "N/A": there is no live
  signal behind any of them yet, so none is invented.

### Why no Sniffer/Warhog services exist yet

The spec that drove this milestone was explicit: don't create empty service
scaffolding to match a future architecture. `apps/api` and `apps/web` are the
only two applications; Sniffer/Warhog become real services (with real
Dockerfiles, real dependencies, real responsibilities) when their first
actual implementation begins — not before. Until then they're routes in the
frontend that render an intentional "in progress" page.

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
