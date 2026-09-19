# 30-day history: deployment and verification

The runtime source of truth is `Settings.market_history_retention_days = 30`
in `apps/api/app/core/config.py`. Pydantic accepts the environment override
`MARKET_HISTORY_RETENTION_DAYS`. The checked-in Compose file does not pass that
variable through: putting it in the root `.env` alone does not override the
container setting. Inspect the effective value without printing credentials:

```sh
docker compose exec api python -c 'from app.core.config import get_settings; print(get_settings().market_history_retention_days)'
```

The old default was 365 days. Bootstrap now uses
`max(history_target_start, now - retention_days)` and skips backward work if
`history_synced_from` already reaches that floor. An empty REST page advances
the backward frontier to this effective floor, not to an obsolete year-old
target. Catch-up also skips expired work following a long outage. In-window
catch-up, shared ingestion/aggregation, live WebSocket handling, frame temporal
rules, and `return_5m` calculation remain unchanged.

Targets and old backward frontiers remain truthful records of prior work;
they are not physical-row inventory. Coverage clips their reconciled interval
to the current window and caps at 100%. Raising retention again is not a
complete rebootstrap procedure: old targets can limit expansion and old
frontiers can describe data already pruned.

## Deploy when ready

No new migration, explicit cleanup command, mass DELETE, or VACUUM FULL is
required. The normal entrypoint still runs existing Alembic migrations.
For this repository's Compose deployment, rebuild and recreate **only the API**:

```sh
docker compose up -d --build --no-deps api
```

This command is operationally destructive through normal retention: it enables
partition cleanup. It was not executed during this review. A plain
`docker compose restart api` keeps the old image and is insufficient to load
these source changes. A host-based deployment needs a process restart after
updating its source. PostgreSQL and web images need no rebuild.

The new reconciler stops year-deep bootstrap as soon as it runs. Retention runs
on the initial successful universe discovery during startup, then after each
successful universe refresh (900 seconds by default). If Bybit discovery fails,
that cycle skips cleanup. Periodic refresh also provisions this and next month's
partitions, so continuous operation does not depend on a monthly restart.

## What happens to the existing database

Member partitions and Sniffer partitions already shared the candle horizon
before this change; their effective retention therefore also changes from 365
to 30 days. `market_frames` headers are not partitioned or pruned. They retain
original completeness counts as historical metadata even after their detailed
members/results expire; an old header is not a promise that full context is
still available. Sniffer results are self-contained values with foreign keys to
headers and instruments, not to candles.

Member candle references are plain timestamps, **not candle foreign keys**.
Equal month cutoffs alone are unsafe: a frame at August 1 00:00 references July
candles, and the latest legal candle can be arbitrarily stale. Cleanup now drops
expired member partitions first, then preserves any candle month referenced by
surviving members. It does not rewrite immutable frames or delete candle rows.

For UTC September 19, 2026, the rolling floor is August 20 and the monthly
cutoff is August 1. Member/result partitions named `*_2026_07` and earlier are
eligible for dropping. Candle partitions through July are candidates, but any
still referenced by retained members are kept. August and September remain;
empty provisioned future partitions remain too. July can therefore remain if
August frames refer to it. Without reference exceptions, monthly rounding adds
less than 31 days beyond the configured 30 days (oldest data less than 61 days
old). Reference-protected months can remain longer, until their last referring
member partition expires; there is no fixed upper bound for stale references.

Dropping whole partitions releases their heap and index files. The reported
~6.4 GB should decrease to the extent it contains eligible, unreferenced months;
an exact saving cannot be predicted from total size alone. Ongoing bootstrap
inside the window and growing member/result tables can offset savings. The
Docker volume itself remains and may not visibly shrink at the host disk-image
level immediately.

Reference checks use surviving member rows, potentially a large scan for each
candidate month. Cleanup holds exclusive candle/member table locks until its
checks and drops commit, to prevent concurrent frame creation from racing the
reference check. This can pause reads/ingestion and frame production during
cleanup; duration depends on member volume and storage. Allow startup to finish,
then check health and recent rows. No live database operation was performed by
this review. PostgreSQL DDL selection/locking was checked with a mocked dialect;
reference predicates were tested against SQLite, not a live PostgreSQL server.

## Read-only verification

Connect using the existing Compose service credentials without exposing them:

```sh
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Run `docs/history_verification.sql` in that session, or paste its queries.
All statements are read-only. The file sets a 30-second statement timeout and
read-only transactions. Run the watermark/latest-row queries again after a few
minutes: persisted watermarks show progress, but do not record HTTP request
bounds, so they cannot prove the running binary never sends a deeper request.
Old backward frontiers alone are expected, not evidence of continued digging.
