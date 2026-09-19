-- Read-only verification. Assumes the effective configured horizon is 30 days.
SET default_transaction_read_only = on;
SET statement_timeout = '30s';
SET TIME ZONE 'UTC';

-- A: total database size (catalog/filesystem accounting).
SELECT current_database(), pg_size_pretty(pg_database_size(current_database())) AS size;

-- B: actual candle partitions, bounds, and heap + index sizes.
SELECT c.relname AS partition,
       pg_get_expr(c.relpartbound, c.oid) AS bounds,
       pg_size_pretty(pg_table_size(c.oid)) AS heap_and_auxiliary,
       pg_size_pretty(pg_indexes_size(c.oid)) AS indexes,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS total
FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
WHERE i.inhparent = 'market_candles'::regclass
ORDER BY c.relname;

-- C + D: oldest ACTUAL candle per timeframe, including inactive instruments.
-- The 1m row answers C. PK-leading instrument/timeframe probes avoid a global
-- tens-of-millions-row GROUP BY. Work scales with instruments x partitions.
SELECT tf.timeframe, min(first_row.open_time) AS oldest_actual
FROM instruments i
CROSS JOIN (VALUES ('1m'), ('5m'), ('15m'), ('1h')) AS tf(timeframe)
CROSS JOIN LATERAL (
    SELECT c.open_time FROM market_candles c
    WHERE c.instrument_id = i.id AND c.timeframe = tf.timeframe
    ORDER BY c.open_time LIMIT 1
) first_row
GROUP BY tf.timeframe ORDER BY tf.timeframe;

-- E: watermark distribution; scans only the small instruments table.
SELECT is_active, count(*) AS instruments,
       count(*) FILTER (WHERE history_synced_from IS NULL) AS uninitialized,
       count(*) FILTER (WHERE history_synced_from <= now() - interval '365 days') AS from_365d,
       count(*) FILTER (WHERE history_synced_from <= now() - interval '30 days') AS from_30d,
       min(history_target_start) AS oldest_original_target,
       min(history_synced_from) AS oldest_backward_frontier,
       min(history_synced_through) AS oldest_forward_frontier,
       max(history_synced_through) AS newest_forward_frontier
FROM instruments GROUP BY is_active;

-- F: repeat this snapshot to compare backward/forward progress.
-- An unchanged old frontier is expected. No request-bound audit is persisted.
SELECT symbol, history_target_start, history_synced_from, history_synced_through,
       greatest(history_target_start, now() - interval '30 days') AS effective_floor,
       history_synced_from > greatest(history_target_start, now() - interval '30 days')
           AS backward_work_remaining,
       history_synced_through < now() - interval '30 days' AS expired_catchup_frontier
FROM instruments WHERE is_active ORDER BY symbol;

-- G: latest 1m candle per active instrument. Repeat to verify advancement.
-- Last-day bound prunes old partitions; NULL flags missing recent data.
SELECT i.symbol, latest.open_time, now() - latest.open_time AS candle_age
FROM instruments i
LEFT JOIN LATERAL (
    SELECT open_time FROM market_candles c
    WHERE c.instrument_id = i.id AND c.timeframe = '1m'
      AND c.open_time >= now() - interval '1 day'
    ORDER BY c.open_time DESC LIMIT 1
) latest ON true
WHERE i.is_active ORDER BY latest.open_time NULLS FIRST, i.symbol;

-- H: latest frames, stored status and actual member counts.
WITH recent AS (SELECT * FROM market_frames ORDER BY frame_time DESC LIMIT 10)
SELECT f.frame_time, f.status, f.expected_instruments, f.available_instruments,
       m.actual_members,
       CASE WHEN f.status = 'building' THEN NULL
            ELSE f.available_instruments = m.actual_members
             AND ((f.status = 'complete' AND f.available_instruments = f.expected_instruments)
               OR (f.status = 'partial' AND f.available_instruments < f.expected_instruments))
       END AS counts_and_status_consistent
FROM recent f
CROSS JOIN LATERAL (
    SELECT count(*) AS actual_members FROM market_frame_members m
    WHERE m.frame_time = f.frame_time
) m ORDER BY f.frame_time DESC;

-- I: recent return_5m output. NULL values are legitimate unavailable results.
-- Recent range + leading frame_time key keep this bounded.
SELECT frame_time, metric, count(*) AS results,
       count(value) AS available_values, max(calculated_at) AS last_calculation
FROM sniffer_results
WHERE frame_time >= now() - interval '10 minutes' AND metric = 'return_5m'
GROUP BY frame_time, metric ORDER BY frame_time DESC LIMIT 10;

-- Optional: potentially EXPENSIVE full surviving-member scan. This shows the
-- oldest referenced candle and why an older candle partition may be protected.
-- The timeout limits execution; omit this on a busy production database.
-- SELECT min(least(open_time_1m, open_time_5m, open_time_15m, open_time_1h))
--     AS oldest_referenced_candle FROM market_frame_members;
