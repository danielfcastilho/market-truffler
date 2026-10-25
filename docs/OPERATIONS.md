# Operations

How to keep Market Truffler collecting, and what is (and isn't) protected
when something fails. Architecture and the research pipeline itself are in
[`ARCHITECTURE.md`](ARCHITECTURE.md); this page is the operator's view.

The one fact everything below follows from: **Market Frames exist only for
moments the stack was running.** Candles, Open Interest and funding can be
re-fetched from Bybit later; the synchronized 5-minute observation record
cannot. Every outage is a permanent hole (Oct 13 00:05 – Oct 24 15:15, 2026
was one).

## What must run

| Component | Runs in | Needed for collection? |
|---|---|---|
| Bybit WebSocket collector, history / Open Interest / funding reconcilers, frame synchronizer, Sniffer | `api` (all started in its lifespan — one process) | **yes** |
| PostgreSQL | `postgres` | **yes** |
| Research continuity worker (daily shards, backup) | `research` | to preserve frames as datasets before retention prunes their sources (~2 weeks) |
| Frontend | `web` | no — viewing only |

Data acquisition cannot be split from the API without code changes: they
are the same process. The research worker needs the same database and the
same data directory.

## Keeping the Mac awake

This Mac's power settings sleep the system **1 minute** after the display
sleeps (`pmset -g`: `sleep 1`, `displaysleep 10`). Sleep freezes Docker, so
collection stops.

```bash
infra/keep-awake.sh start    # prevent idle sleep (no-op if already active)
infra/keep-awake.sh status   # real state; exit 0 only when active
infra/keep-awake.sh stop     # back to normal sleep behaviour
```

It runs macOS's own `caffeinate -i -s` detached (survives closing the
terminal), keeps at most one such process (pidfile in `$TMPDIR` plus a
check of the actual process, so it never kills anything else), and changes
no system setting. `status` shows the power assertions macOS actually
holds for it.

It does **not** prevent: closing a MacBook's lid (sleeps anyway unless in
clamshell mode with an external display and power), shutdown, restart,
logout, power loss, or Docker Desktop quitting. On battery only idle sleep
is prevented (`-s` needs AC power). It does not survive a reboot or logout.
Vitals cannot see the host's power state, so it shows no keep-awake
indicator; Vitals' **Missed frames (24h)** row shows the consequence of any
interruption instead.

**Optional, not installed — persistent across logins.** Only if you want
it: a user LaunchAgent that holds the same assertion from login on.

```bash
cat > ~/Library/LaunchAgents/com.market-truffler.keep-awake.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.market-truffler.keep-awake</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/caffeinate</string><string>-i</string><string>-s</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
EOF
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.market-truffler.keep-awake.plist   # enable
launchctl bootout gui/$(id -u)/com.market-truffler.keep-awake                                  # disable
rm ~/Library/LaunchAgents/com.market-truffler.keep-awake.plist                                 # remove
```

It holds the assertion whether or not the stack runs; don't combine it
with `keep-awake.sh` (both would just hold the same assertion).

## Restart behaviour

All four services use `restart: unless-stopped`.

| Event | What happens |
|---|---|
| A container crashes | Docker restarts it. `api` re-runs migrations (no-op) and resumes; the reconcilers backfill candles/OI/funding for the gap; frames for the gap are lost. |
| Bybit unreachable | No restart needed: the WebSocket reconnects with backoff (1s → 30s), reconcilers and the frame loop catch per-iteration errors. Frames keep being built; their completeness drops; reconcilers backfill candles/OI afterwards. |
| Docker Desktop restarts | Running services come back (`unless-stopped`). `depends_on` ordering is **not** applied on daemon restarts: if `api` starts before PostgreSQL is ready its migration fails and Docker restarts it until it succeeds; `research` waits for the schema to match before doing anything. |
| Mac restarts | **Nothing runs until Docker Desktop is opened** — its "Start Docker Desktop when you sign in" is off (`AutoStart: false`), and a login is required first. Enable it in Docker Desktop → Settings → General if you want the stack back after a reboot (your call; it's a host setting). `keep-awake.sh` must be started again. |
| Research worker stopped mid-shard | The child is terminated, nothing is published, no failure is counted; the next pass regenerates the day (see `ARCHITECTURE.md`). |

**Restarting `api` deliberately.** Wait until about 2.5 minutes after a
5-minute boundary (frames finalize about T+30s and Sniffer measures them
at T+60s, finishing about T+100s), then verify the *next* frame has
Sniffer results, not only that it finalized. An analysis interrupted
anyway is no longer lost: on startup Sniffer recovers finalized frames of
the last 24h without results (at most 24, oldest first, labelled
`analysis = 'recovery'`). Vitals shows "Unanalyzed frames (24h)" for any
still missing.

The research worker does **not** need to be stopped or waited for: a
restart during shard generation is safe (see *Partition maintenance*
below). There is one `api` process, so recreating it still pauses
collection for the restart itself (6.9 s from recreate to the frame loop
running on 2026-10-25, with a shard being generated: migrations, universe
discovery, collector reconnect) — a frame whose boundary falls in
that window can still be lost, hence the timing above. Zero-downtime
deploys would need a second collector, which this stack does not have.

```bash
docker compose up -d --build --no-deps api      # deploy
docker compose logs -f api | grep -E 'frame_synchronizer_started|partition_'
```

## Partition maintenance

`market_candles`, `market_frame_members` and `sniffer_results` are
partitioned by month (`app/services/partition_manager.py`,
`app/services/partition_maintenance.py`). Creating or dropping a partition
needs an ACCESS EXCLUSIVE lock on the table, which waits for every open
reader — and, while waiting, makes every later reader and writer (frame
building included) queue behind it.

**The 2026-10-25 outage.** Each startup created the partitions for the
last three months. Retention (30 days) keeps only months from the cutoff
month on, so every restart re-created two already-expired, empty months,
and retention dropped them again two seconds later. At 14:28 UTC that
creation waited, with no timeout, behind the research worker's shard read
on `market_frame_members`. Startup hung for seven minutes, frames 14:30
and 14:35 were lost, and it only continued once `research` was stopped.

**Required — before anything ingests.** The current month's partition of
each table (live candles, frames and Sniffer results are written there).
Startup checks the catalog; when they exist (always, normally) it takes
no lock at all. If one is missing, startup creates it with a bounded lock
wait and retries (1, 2, 5, then every 10 s) until it exists. Meanwhile the
API is not ready (`/health` does not answer yet, the container is not
healthy) and nothing is collected — never silently. A non-contention error
fails startup and Docker restarts it.

**Deferrable — in the background after each universe refresh (15 min).**
The rest of the retained window (next month's partition, created about a
month before it is needed; older retained months the history backfill
writes into) and retention drops. Expired months are never created.
Retention takes its exclusive locks only when a month is actually
droppable; a candle month still referenced by retained frames is
recognised without them.

**Bounded waits.** Every partition statement runs with `SET LOCAL
lock_timeout` = `PARTITION_LOCK_TIMEOUT_MS` (default 2000 ms). It ends with
its transaction, so it never stays on a pooled connection, and other
statements are unaffected. A lock timeout (SQLSTATE 55P03) or a deadlock
(40P01) defers the operation: the transaction is rolled back and it is
retried after 15, 30, 60, 120 and 240 s, then at the next universe
refresh. Only one maintenance run exists at a time. A restart forgets
nothing: what is missing is re-derived from the catalog. Any other error
(missing table, permissions, anything unexpected) is reported as
*Failed* and never retried as contention. A second process creating the
same partition at the same moment ("already exists", 42P07) counts as
done.

**Impact on collection.** While a partition statement waits (at most the
timeout), queries on that table queue behind it — at most ~2 s of delay to
frame building, then the statement gives up.

**Vitals → MARKET → Partition maintenance**

| Shown | Meaning | Action |
|---|---|---|
| Healthy (time) | Window present, retention done | — |
| Not run yet | Just started; the first pass follows the first universe refresh | — |
| Deferred — lock_timeout …; retry N at … | Waiting for a lock on work that can wait; retrying | None: it resolves when the reader (usually a research shard) finishes |
| Deferred — …; retried at the next universe refresh | Retries exhausted; tried again within 15 min | Only if it persists for hours |
| Failed — category | Not contention: `missing_schema`, `permission`, `unexpected` | Read `partition_operation_failed` in the api log |
| Required partition unavailable — ingestion blocked | A table has no partition for the current month: inserts fail. `/ready` returns 503 | Find what holds the lock (below); check the log |

The categories never include database messages or credentials; the api
log has the full error.

**Fallback.** If a required partition stays blocked, find the holder:

```bash
docker compose exec postgres psql -U truffler -d market_truffler -c \
  "select pid, state, now()-xact_start as age, left(query,80) from pg_stat_activity
   where pid = any(pg_blocking_pids((select pid from pg_stat_activity
   where wait_event_type = 'Lock' limit 1)));"
```

If it is the research worker, `docker compose stop research` releases it
(safe mid-shard: the day is regenerated) — then `docker compose up -d
research` once the api is healthy. This is an exceptional fallback, no
longer part of a normal deploy.

**Ports.** `api` and `web` publish on `127.0.0.1` by default
(`API_BIND`/`WEB_BIND` in `.env`); set them to `0.0.0.0` only to reach the
app from another machine on purpose. Interactive API docs (`/docs`) are
served only with `APP_ENV=development`.

**Research data health.** OINK CORP → Data health shows the frame history,
research shards, temporal-contract versions and each prospective
registration's readiness (read-only; see
[RESEARCH_DATA_HEALTH.md](RESEARCH_DATA_HEALTH.md)). The `research` worker
mounts campaigns, experiments and ranking specs read-only for that report.

**Live/replay parity.** `docker compose exec api python -m
app.scripts.parity_diagnostic --frames 12` compares the latest live
Sniffer results with a replay of the same frames (read-only, features
only); see [TEMPORAL_CONTRACT.md](TEMPORAL_CONTRACT.md).

`docker compose ps` shows `research` as healthy only while its minute
heartbeat is fresh (a healthcheck — visibility only; Docker does not
restart unhealthy containers). `api` is healthy when `/health` answers;
`web` and `research` start only after `api` is healthy on `compose up`.

## Backups

### What is protected

| Data | Where | Backed up? | Recoverable without a backup? |
|---|---|---|---|
| Research datasets (Parquet + manifest) | `research/oink_corp/data/` | **only once `RESEARCH_BACKUP_DIR` is configured** — currently **not** | **No**: built from frames, which expire with retention |
| Experiment run records | `research/oink_corp/data/runs/` | same | No (results of past runs) |
| Prospective registrations and their exposure events | `research/oink_corp/data/evaluations/` | same | **No** — a registration's `registered_at` and exposure log cannot be recreated honestly |
| Campaigns, experiment specs, ranking candidates, qualification specs, knowledge ledgers (campaign and model) | `research/oink_corp/{campaigns,experiments,ranking,knowledge}/` | Git — protected once committed **and pushed** (all pushed as of 2026-10-25) | From the Git remote |
| Worker operational state (`continuity/`: status, retry state, verification, alerts, backup reports) | `research/oink_corp/data/continuity/` | no (excluded on purpose) | Yes — re-derived by the next pass |
| Candles | PostgreSQL | no | Yes — re-fetched from Bybit by the history reconciler (30-day window) |
| Open Interest, funding | PostgreSQL | no | Yes within the retention window — the reconcilers fetched the full window on first start and refilled 10-day-old OI gaps (2026-10-24) |
| Market Frames, frame members, Sniffer results | PostgreSQL | no | **No** — frames are never backfilled. What matters for research is preserved in the daily shards |
| Users | PostgreSQL | no | Recreate accounts |
| Code, experiment specs | Git | via your Git remote | — |

The database (~14 GB, mostly candles and Open Interest) lives in the Docker
volume `market-truffler_postgres_data`, inside Docker Desktop's VM disk on
the Mac's internal SSD — the same physical disk as the research data. **If
that disk fails today, everything not in Git is lost**; a fresh stack would
re-fetch ~30 days of market data from Bybit by itself, but all research
datasets, run records and past frames would be gone.

Research-dataset backups are not application disaster recovery. The minimum
for meaningful recovery is (1) the research backup below, which covers
everything irreplaceable once frames are preserved as shards; plus,
optionally, (2) a periodic `pg_dump` of the small tables that are not
reconstructable (`market_frames` ≈ 4 MB, `instruments`, `users`) to the same
destination — not implemented; whole-database backup is not worth it while
its bulk can be re-fetched from Bybit.

### Four separate states

- **Configured:** `RESEARCH_BACKUP_DIR` points at a marker-initialized
  destination.
- **Completed:** the last backup run attempted every item (Data Health:
  "last attempt").
- **Verified:** every in-scope item's copy matched its checksums ("last
  verified backup", i.e. the last run with no item error). A copied file is
  never called verified before its hash is compared, and copies are
  re-verified daily.
- **Recovery tested:** the backup was restored into an isolated temporary
  directory and every file checked. This runs daily when configured, or on
  demand with `--recovery-test`.

They are reported separately in Data Health → Research backups, and as
alerts in Vitals → OINK CORP:

| alert | severity |
| --- | --- |
| `backup_not_configured` | warning |
| `backup_destination_unavailable` | warning; critical if no verified backup exists |
| `backup_failed`: an item could not be copied | warning |
| `backup_incomplete`: items without a verified copy | warning |
| `backup_overdue`: no fully successful run in 36 h | warning |
| `backup_integrity_failed`: a source or copy doesn't match its checksums | critical |
| `recovery_untested` / `recovery_test_unavailable` / `recovery_test_overdue` | warning |
| `recovery_test_failed`: restored items missing or corrupt | critical |

The alerts use the same stable ids and lifecycle as the continuity alerts:
active while true, resolved automatically, and unaffected by restarts.

**Recovery test.** `--recovery-test` runs the same check on demand; the
worker also runs it daily (`RESEARCH_RECOVERY_TEST_INTERVAL_HOURS`). For
each protected item it:

1. verifies the copy against its `BACKUP_CHECKSUMS.json` and the source
   hashes the last backup recorded;
2. restores it into a fresh `market-truffler-recovery-test-*` temporary
   directory, never the data directory;
3. re-hashes the restored files;
4. validates restored datasets (`read_dataset`, loading only `frame_time`;
   a dataset reaching into a reserved prospective period is checked by hash
   only and never parsed);
5. deletes the temporary directory.

It covers the whole archive while it is under 2 GiB. Above that it runs a
**representative** test: every registration and exposure event plus other
items up to the bound, labelled as such and never as a full
disaster-recovery test.

**Protected data.** Backup, recovery test and restore copy and hash bytes;
they never read outcome labels of reserved datasets, write exposure events,
or modify registrations, ledgers or manifests. The destination holds the
same protected files as the data directory. Mount it only into the
`research` container, keep it as private as the data directory, and never
expose it through another service.

### Activate independent backup

No independent storage was available when this was written (no external
disk or NAS mounted, no Time Machine destination). Another folder on the
internal SSD is not a backup, and a cloud-synced folder is not a verified
one. Once you have one:

1. Create a directory on it, e.g. `/Volumes/<drive>/market-truffler-research`.
2. Confirm it is independent storage, then mark it:
   `touch /Volumes/<drive>/market-truffler-research/.market-truffler-backup`
3. Create `docker-compose.override.yml` next to `docker-compose.yml`
   (gitignored; Compose merges it automatically):
   ```yaml
   services:
     research:
       environment:
         RESEARCH_BACKUP_DIR: /app/research_backup
       volumes:
         - /Volumes/<drive>/market-truffler-research:/app/research_backup
   ```
4. When Vitals shows no shard generating: `docker compose up -d research`.
5. `docker compose exec research python -m app.scripts.research_continuity --backup`
   — exit status 0 only when every record's copy is verified. Vitals'
   **Backup protection** then shows "All N verified".

From then on every pass backs up new records and re-verifies existing
copies daily. If the drive is unplugged, Vitals shows **Failing: … not
mounted** (the marker file is what distinguishes the drive from an empty
directory). External drives on macOS must be connected and unlocked before
the `research` container starts, or the mount is empty.

### Restore

```bash
docker compose exec research python -m app.scripts.research_continuity --restore shard_s15m_20261012
docker compose exec research python -m app.scripts.research_continuity --restore runs/<experiment>/<run>
```

Each copy is checked against its `BACKUP_CHECKSUMS.json`, restored
atomically, and a dataset must also pass `read_dataset`. Restore never
replaces an existing directory. After losing the data disk, recreate an
empty `research/oink_corp/data/`, mount the backup as above, and restore
each record; `--status --verify-all` re-verifies them all.

Rehearsed on 2026-10-24 against copies of all real records (3 datasets, 3
run records): backup verified, a second run re-verified without rewriting
anything, a dataset and a run record restored byte-identical to production.
No real backup exists until step 5 above has run.

### Disaster recovery (data disk lost)

Scientific state is a set of related artifacts. A dataset without its
registration, manifest or exposure history is not a recoverable study.

1. **Find the latest valid backup.** Mount the backup drive and check its
   `.market-truffler-backup` marker. Its `BACKUP_CHECKSUMS.json` files
   verify each copy independently of the lost disk.
2. **Restore into isolation first.** Mount the backup as in "Activate", set
   `--data-dir` to a new empty directory (not `research/oink_corp/data`),
   and `--restore` every item into it. Each restore verifies checksums and
   refuses to replace anything.
3. **Verify** with `--status --verify-all --data-dir <that directory>` and
   `--recovery-test`.
4. **Check the scientific set.**
   - Every `evaluations/<id>/registration` is present with all of its
     `events/NNNNNN`. The exposure log must be complete: a missing event
     can make a used period look unexposed.
   - The specs and knowledge ledgers they reference come from Git:
     `git pull`, then `prospective_evaluation --verify` /
     `truffle_evaluation --verify` / `knowledge_ledger --verify` /
     `model_knowledge --verify`.
5. **Only then move** the verified directory into place as
   `research/oink_corp/data/`, with the stack stopped. Never copy over an
   existing data directory; nothing automated does this.
6. **Resume:** `docker compose up -d`. The worker re-derives its
   operational state; frames lost with the database are not recoverable,
   and Data Health will show those gaps.

## Always-on hosting

| | Local Mac + keep-awake | Dedicated always-on machine at home (e.g. a small desktop) | VPS / cloud |
|---|---|---|---|
| Collection reliability | Breaks on lid close, reboot, Docker quit, travel | High; breaks on power/network outages at home | Highest; provider uptime |
| Effort | None (today) | One-time setup; same `docker compose` | Setup plus security: exposing `api`/`web` needs TLS + auth hardening, or keep them private (SSH tunnel) |
| Storage | Fine (650 GB free) | Any small SSD; growth ≈ 14 GB DB (bounded by 30-day retention) + ~15 MB per full-day shard (~5–6 GB/year) | ≥ 40 GB disk; same growth |
| Backup | Needs an external drive | Second disk or NAS in the same house | Provider snapshots are *not* independent; still copy research data off-host |
| Cost | €0 | hardware you own or buy once, plus electricity | small 2–4 vCPU / 4–8 GB VPS: third-party listings put this around €8–20/month after Hetzner's 2026 increase; figures conflict and must be checked on the provider's page |

**Recommendation.** Keep running locally with `keep-awake.sh` and an
external backup drive now — it closes the avoidable gaps at no cost. If
uninterrupted collection becomes important (research needs months of
contiguous days), move the whole stack unchanged — `postgres`, `api`,
`research` (`web` optional) — to a dedicated always-on machine or a small
VPS. No code change is needed for either; the database can start empty
(the reconcilers re-fetch the market-data window) and the research data
directory moves with `rsync`/restore.
