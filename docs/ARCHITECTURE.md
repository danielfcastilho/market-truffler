# Architecture

This document describes two things: the **future conceptual system** Market
Truffler is being built toward, and the **actual state** of this foundation
milestone. Don't confuse the two — most of what's below is not implemented
yet, by design.

## The future system

```
                    BYBIT
                      │
                      ▼
              🐽 SNIFFER — DATA
   (exchange connectivity, market-data
    acquisition, normalization, storage)
                      │
                      ▼
            🍄 TRUFFLER — ANALYSIS
  (features, desirability functions, pillars,
     Entry Fitness, Long/Short rankings)
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
              TRUFFLER + WARHOG
```

OINK CORP is where the strategy is developed and validated. Sniffer →
Truffler → Warhog is where it runs in production. OINK CORP is not a
production runtime dependency — nothing in `docker-compose.yml` builds or
starts it.

### Domain responsibilities (future)

**🐽 Sniffer — Data.** Exchange connectivity, historical backfills,
WebSocket market streams, normalization, storage, data quality.

**🍄 Truffler — Analysis.** Turns market data into strategy-specific
opportunity intelligence: features, desirability functions, pillars, Entry
Fitness, Long ranking, Short ranking.

**🐗 Warhog — Trading.** Entries, position sizing, Martin Gale progression,
active-position management, recovery, emergency behavior, hedging, exits,
orders, fills, reconciliation.

**🧬 OINK CORP — Research.** Quantitative experiments, feature research,
backtests, Martin Gale simulation, calibration, ablation, walk-forward
validation. A repository boundary and a conceptual home for research, not a
service.

None of the above is implemented in this milestone. No indicators, features,
pillars, desirability functions, weights, Entry Fitness, rankings, Martin
Gale parameters, entry/exit/hedge rules, or live execution architecture have
been decided. Those are later engineering/research milestones, and this
codebase makes no assumptions about them.

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
  Owns the `User` model, session issuance/verification, and the only
  database-touching endpoints that exist right now (`/health`, `/ready`,
  `/api/me`, `/api/system`, `/api/auth/login`, `/api/auth/logout`).
- **`postgres`**: one table (`users`). No market-data, ranking, feature, or
  trade-related schema exists — those get designed when their requirements
  are known, not speculatively now.
- **`research/oink_corp`, `packages/shared`, `infra`**: boundaries reserved
  for future work (see each directory's own README). Deliberately empty.

### Why no Sniffer/Truffler/Warhog services exist yet

The spec that drove this milestone was explicit: don't create empty service
scaffolding to match a future architecture. `apps/api` and `apps/web` are the
only two applications; Sniffer/Truffler/Warhog become real services (with
real Dockerfiles, real dependencies, real responsibilities) when their
first actual implementation begins — not before. Until then they're four
routes in the frontend that render an intentional "in progress" page.

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
