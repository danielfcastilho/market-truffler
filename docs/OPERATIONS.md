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

**Ports.** `api` and `web` publish on `127.0.0.1` by default
(`API_BIND`/`WEB_BIND` in `.env`); set them to `0.0.0.0` only to reach the
app from another machine on purpose. Interactive API docs (`/docs`) are
served only with `APP_ENV=development`.

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
