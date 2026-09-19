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

### Bybit connectivity and the market universe (this milestone)

The first real piece of the MARKET area now exists: a read-only connection
to Bybit's public REST API, used only to answer "can we reach Bybit?" and
"how many instruments are in the universe we care about?" It is not market
data acquisition — no tickers, klines, or WebSocket streams are consumed —
and it does not make Sniffer operational.

```
app/integrations/bybit/   HTTP boundary: base URL, timeouts, the retCode
                           envelope. The only place that knows Bybit's wire
                           format. Scoped to public REST today; a future
                           WebSocket client or additional REST calls can
                           live alongside it without a redesign.
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

Bybit is treated as an external dependency: unreachable Bybit never fails
`/health` or `/ready`, and never stops the API from starting. Vitals'
MARKET section reflects this — "Bybit connectivity" and "Symbols tracked"
are real, sourced from `/api/market/status`; "Market data," "Last market
update," and "Data freshness" stay a hardcoded "N/A," since no market data
is consumed yet.

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
  `/api/market/status`, which talks to Bybit's public REST API rather than
  the database.
- **`postgres`**: one table (`users`). No market-data, ranking, feature, or
  trade-related schema exists — those get designed when their requirements
  are known, not speculatively now.
- **`research/oink_corp`, `packages/shared`, `infra`**: boundaries reserved
  for future work (see each directory's own README). Deliberately empty.
- **Vitals** (`apps/web/src/app/(protected)/vitals/page.tsx`): reads
  `/health`, `/ready`, `/api/system`, and now `/api/market/status`.
  Status/health logic is a pure module (`apps/web/src/lib/vitals.ts`, unit
  tested) kept separate from the page's presentation, the same pattern as
  `lib/route-guard.ts`. The SYSTEM section reflects real signals (API
  liveness, readiness, database connectivity, uptime, environment, backend
  version); in MARKET, "Bybit connectivity" and "Symbols tracked" are now
  real too. Everything else — the rest of MARKET, and all of 🐽 SNIFFER,
  🐗 WARHOG, and 🧬 OINK CORP — is hardcoded to "N/A": there is no live
  signal behind it yet, so none is invented.

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
