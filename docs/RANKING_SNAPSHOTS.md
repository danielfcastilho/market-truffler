# Published ranking snapshots

Sniffer → Rankings → **History**. It shows the Long/Short rankings and
experimental Truffles exactly as Sniffer published them. Implemented in
`apps/api/app/services/ranking_snapshots.py` (table `ranking_snapshots`,
migration `d5e7f9a1b3c5`).

## What counts as published, and when it is captured

`GET /api/sniffer/rankings` serves the ranking of the newest analyzed frame.
A frame's ranking therefore becomes available when Sniffer commits that
frame's analysis, provided it is the newest one.

`Sniffer.analyze_frame` calls `RankingPublisher.publish` right after
committing. The publication time is taken at that moment, and the response
comes from the same `live_rankings` call the endpoint makes. That call also
fills the endpoint's cache, so nothing is computed twice and no timer
exists.

- A frame that was not the newest when analyzed (an older frame recovered
  after a restart) was never served live, so it is not captured.
- "Published" means the system made it available, not that anyone looked.
- The frame time is the 5-minute boundary the ranking describes. It
  appeared at its **publication** time, about T+2 min (see
  [TEMPORAL_CONTRACT.md](TEMPORAL_CONTRACT.md)), and both times are shown.

A snapshot is **not a replay**: nothing is reconstructed with today's
models. It is not a backtest either: no outcome is computed. Rankings
published before this capture existed were never stored and are not
backfilled; history starts with the first publication after deployment.

## What is preserved

Each row stores:

- the frame time, the analysis instant and provenance (`live` / `recovery`),
  the publication time and the row's creation time;
- the temporal contract version;
- each direction's ranking model and qualification policy ids and spec
  hashes, the qualification status (`evaluated` / `withheld` /
  `unavailable`) and the Truffle count (null unless evaluated);
- the schema version;
- the published response itself, as gzip-compressed JSON with its sha256:
  - every ranked row with its score at full float precision;
  - contributions and context;
  - each row's **full qualification checks** (observed value, threshold,
    outcome);
  - the Truffles, diagnostics, exclusions, and frame
    completeness/freshness as published.

Market frames are not copied.

## Immutability

- A publication is identified by its frame, its analysis instant and the
  four spec hashes (a unique constraint).
- Rows are inserted, never updated or deleted:
  - capturing the same publication again is a no-op;
  - a different payload for the same identity is a **conflict**, logged and
    counted, and the original is kept;
  - a re-analysis or a spec change is a new publication of the same frame.
- Model or policy changes never rewrite history.
- The duplicate/conflict test compares the published content: the digest
  excludes the capture instant's freshness fields (`age_seconds`, `stale`,
  `stale_reason`), which are still stored. Schema-1 rows (the first four
  captures) hash the whole payload and are kept unchanged.
- Each capture is one INSERT, so a partial row is never readable. Ids are
  ordered but not contiguous: Postgres consumes one on a refused duplicate.
- A capture failure never affects analysis or the live endpoint and is
  never reported as captured. It is logged
  (`ranking_snapshot_capture_failed`) and counted in Vitals → SNIFFER →
  *Published snapshots*.
- Nothing is deleted automatically.

## Comparisons

The detail endpoint compares a snapshot with the **previous available
published snapshot**, which may be more than one frame earlier, using only
the two stored payloads:

- top-10 entries and exits;
- rank movement (↑/↓/–, or new) for the current top 10 and the Truffles;
- Truffles gained and lost, and the count change.

Rules:

- The frame gap is always reported, and only a 5-minute gap is called
  consecutive.
- A changed model or policy hash is flagged, and Truffle changes across a
  policy change are marked not comparable.
- A `withheld` or `unavailable` qualification is never compared as zero
  Truffles.

Movements describe what was published. They are not evidence of
predictive performance.

## API

- `GET /api/sniffer/rankings/history?limit=&before=`: summaries, newest
  first by publication time (`before` is the last id of the previous page),
  at most 100 per page, never payloads.
- `GET /api/sniffer/rankings/history/{id}[?symbol=]`: the stored ranking,
  its previous and next snapshots, and the comparison.

Both require authentication, are read-only and never recompute anything.

## Size

One snapshot of the current ~800-coin universe, both directions with full
explanations, compresses to about 0.2 MB. That is roughly 60 MB a day, or
about 20 GB a year at the current 5-minute cadence. Nothing prunes it yet.
If that becomes a concern, the options are dropping per-row checks for
non-Truffles or archiving old rows to the research backup. Neither is
implemented.
