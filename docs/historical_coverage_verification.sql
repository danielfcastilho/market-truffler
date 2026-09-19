-- Read-only; instruments only. Compare the intended 30-day policy with the
-- stale 365-day image found during investigation. No candle table scan.
WITH parameters AS (
    SELECT statement_timestamp() AS as_of, days
    FROM (VALUES (30), (365)) horizons(days)
), windows AS (
    SELECT p.as_of, p.days, i.*,
           greatest(i.history_target_start,
                    p.as_of - p.days * interval '1 day') AS effective_start
    FROM parameters p CROSS JOIN instruments i
    WHERE i.is_active
), scores AS (
    SELECT *, CASE
        WHEN history_target_start IS NULL OR history_synced_from IS NULL
          OR history_synced_through IS NULL THEN NULL
        WHEN effective_start >= as_of THEN 1.0
        ELSE least(1.0, greatest(0.0,
            extract(epoch FROM (least(history_synced_through, as_of)
                              - greatest(history_synced_from, effective_start)))
            / extract(epoch FROM (as_of - effective_start))))
        END AS coverage
    FROM windows
)
SELECT days AS retention_days, max(as_of) AS measured_at,
       count(*) AS active, count(coverage) AS initialized,
       count(*) FILTER (WHERE history_synced_from <= effective_start) AS backward_complete,
       min(history_synced_through) AS oldest_forward_frontier,
       max(history_synced_through) AS newest_forward_frontier,
       avg(coverage) AS api_fraction,
       round(100 * avg(coverage), 1) AS vitals_percent
FROM scores GROUP BY days ORDER BY days;
-- With no active instruments this yields no rows; the API returns null (N/A).
-- With active instruments but no initialized triples, avg(coverage) is NULL.
