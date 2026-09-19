# Historical coverage investigation — September 19, 2026

## Finding

The checked-out source was clean at commit `592dbf7`, with retention default 30.
Read-only inspection of the running API container found:

- `Settings.model_fields['market_history_retention_days'].default`: **365**.
- Effective `get_settings().market_history_retention_days`: **365**.
- No environment variable overriding that setting.
- 771 active instruments.
- At approximately 21:25 UTC, the current runtime calculation gave ~21.1%.
- On the same later snapshot, 30 days gave **0.9999189582623401** (Vitals **100%**),
  whereas 365 days gave **0.21115031265420145** (Vitals **21.1%**).
- Forward frontiers were only a few minutes behind the observation time.

The running image is stale. The reported earlier 20.7% is consistent with the
same old policy while reconciliation continues; its exact earlier snapshot was
not available. The metric was evaluated directly using the running container's
service and persisted instruments, not through an authenticated HTTP response.
No deployment, watermark modification, or live database mutation was performed.
Database reads explicitly used read-only transactions and a statement timeout.

## Formula before and after review (unchanged)

For each active instrument with all three watermarks:

```
start = max(history_target_start, now - retention_days)
from  = max(history_synced_from, start)
through = min(history_synced_through, now)
score = clamp(max(0, through - from) / (now - start), 0, 1)
```

A zero/negative target span returns 1. Missing watermark triples are excluded;
no computable scores returns null/N/A. The result is the unweighted arithmetic
mean of computable per-instrument scores, not a fraction of completed symbols
and not a duration-weighted mean. Future frontiers and inverted intervals cannot
produce scores outside [0, 1].

The supplied SQL proves backward completion under 30 days only. It neither
checks forward coverage nor proves that the running process uses 30 days.
With every 30-day backward frontier complete, a forward frontier 23.79 days
behind now could legitimately yield 20.7%. That is not the state observed here.

These watermarks record reconciled time, including exchange-confirmed empty
ranges; they are not a count or inventory of actual candles. Manual candle
DELETE/VACUUM does not update the metric, and physical history extents alone
cannot establish per-instrument reconciliation completeness.

## End-to-end trace

1. `InstrumentRepository.list_active()` reads only active instrument rows.
2. `/api/market/status` calls `compute_historical_coverage` with a fresh UTC
   timestamp and `get_settings().market_history_retention_days`.
3. `MarketStatus.historical_coverage` serializes that fraction or null unchanged.
4. `server-api.ts` fetches this endpoint with `cache: 'no-store'`.
5. The Vitals server page passes it to `buildVitalsSections`.
6. `formatCoveragePercent` clamps, multiplies by 100 once, and rounds to one
   decimal (100 is shown as `100%`). An already-open page does not poll; reload
   it for a fresh server render.

Settings are cached per process with `lru_cache`. They are not recalculated from
changed environment variables inside an already-running process. The observed
problem is stronger than a stale cache: the image's class default itself is 365.

## Newly listed instruments

A persisted instrument-specific target eight days ago is correctly honored and
can yield 100%. However, current discovery does **not** store exchange listing
time or automatically set that target to the listing date: it initially writes
`discovery_now - retention_days`. When backward REST reaches an empty range,
bootstrap marks the effective floor reconciled without fabricating candles or
rewriting the target. Thus a fully reconciled new listing can still reach 100%,
because confirmed absence before listing is treated as reconciled. Partial
coverage is a reconciliation-progress estimate, not an available-candle ratio.
The old unit-test description claiming target tightening was inaccurate and
has been corrected. No new listing metadata or schema was introduced.

## Changes and verification

No calculation, runtime configuration, frontend behavior, or reconciliation
logic changed. Added explicit 30-day cases A–G, null-triple aggregation tests,
an unequal-window aggregation test, runtime 30/60-day API wiring tests with
inactive-instrument exclusion, a default-policy regression test, and a Vitals
`0.207 -> 20.7%` rendering test. Clarified the service's formula documentation.

Use `historical_coverage_verification.sql` to compare SQL against
`/api/market/status`'s `historical_coverage` and a freshly reloaded Vitals page.
The query reads only `instruments`; the two rows compare 30 and 365 days.
Small timing differences between SQL and HTTP are normal.

## Deployment needed, not a formula patch

When ready, load the existing 30-day source into a rebuilt API container:

```sh
docker compose up -d --build --no-deps api
```

This was **not run**. A plain restart retains the old image. No new migration,
frontend rebuild, manual DELETE, or VACUUM is required for this metric.
Recreating the API also enables the previously implemented retention mechanism;
see `HISTORY_RETENTION.md` for its cleanup behavior.

Verify the effective runtime setting after deployment:

```sh
docker compose exec -T api python -c 'from app.core.config import get_settings; print(get_settings().market_history_retention_days)'
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < docs/historical_coverage_verification.sql
```

## Final results

- Focused coverage/API tests: **38 passed**.
- Full backend suite: **220 passed**.
- Ruff: passed. Mypy: passed, 61 source files.
- Frontend typecheck/lint: passed. Frontend tests: **38 passed**.
- `git diff --check`: passed.
- The provided SQL was executed successfully in an explicitly read-only
  PostgreSQL session at **21:28:31 UTC**: all 771 active instruments initialized;
  30-day coverage **0.99991723051802813406**, 771 backward-complete;
  365-day coverage **0.21384637788139764589**, 85 backward-complete.
  These display as **100%** and **21.4%**, respectively. Increasing old-policy
  coverage between snapshots reflects ongoing reconciliation, not a code change.
