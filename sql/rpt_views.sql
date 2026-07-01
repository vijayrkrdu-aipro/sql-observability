/* ============================================================================
   SQL Server Fleet Observability — rpt.* reporting views
   Target: the REPOSITORY instance. Deploy AFTER repo_schema.sql and
   workload_attribution.sql PART A (these views read dim_workload_class/fact_workload).
   Power BI reads ONLY rpt.* (guardrail #7) — never the raw dbo.fact_* tables directly.
   Safe to re-run: every view uses CREATE OR ALTER.
   ============================================================================ */

USE DBA_Observability;
GO

IF SCHEMA_ID('rpt') IS NULL
    EXEC('CREATE SCHEMA rpt;');
GO

/* ---------------------------------------------------------------------------
   rpt.cpu_timeline — straight select of fact_cpu.
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW rpt.cpu_timeline AS
SELECT source_instance, sample_time_utc, sql_cpu_pct, other_cpu_pct, idle_pct
FROM dbo.fact_cpu;
GO

/* ---------------------------------------------------------------------------
   rpt.cpu_hour_heatmap — avg SQL CPU% by day-of-week x hour-of-day (UTC).
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW rpt.cpu_hour_heatmap AS
SELECT source_instance,
       DATENAME(weekday, sample_time_utc) AS day_of_week,
       DATEPART(hour,   sample_time_utc)  AS hour_of_day,
       AVG(CAST(sql_cpu_pct AS DECIMAL(5,2))) AS avg_sql_cpu_pct
FROM dbo.fact_cpu
GROUP BY source_instance, DATENAME(weekday, sample_time_utc), DATEPART(hour, sample_time_utc);
GO

/* ---------------------------------------------------------------------------
   rpt.wait_deltas — cumulative -> per-snapshot delta via LAG, restart-safe
   (delta discarded, not negative, when the counter resets below its previous value).
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW rpt.wait_deltas AS
SELECT source_instance, snapshot_time_utc, wait_type,
       CASE WHEN wait_ms_cum >= prev THEN wait_ms_cum - prev ELSE wait_ms_cum END AS wait_ms_delta,
       CASE
           WHEN wait_type LIKE 'LCK[_]M%'                                             THEN 'Lock'
           WHEN wait_type IN ('CXPACKET', 'CXCONSUMER')                                THEN 'Parallelism'
           WHEN wait_type LIKE 'PAGEIOLATCH%' OR wait_type IN
                ('IO_COMPLETION', 'ASYNC_IO_COMPLETION', 'WRITELOG', 'BACKUPIO')       THEN 'IO'
           WHEN wait_type IN ('RESOURCE_SEMAPHORE', 'CMEMTHREAD',
                              'RESOURCE_SEMAPHORE_QUERY_COMPILE')                      THEN 'Memory'
           WHEN wait_type IN ('ASYNC_NETWORK_IO', 'NET_WAITFOR_PACKET')                THEN 'Network'
           WHEN wait_type IN ('SOS_SCHEDULER_YIELD', 'THREADPOOL')                     THEN 'CPU'
           ELSE 'Other'
       END AS wait_category
FROM (
    SELECT *, LAG(wait_ms_cum) OVER (PARTITION BY source_instance, wait_type
                                     ORDER BY snapshot_time_utc) AS prev
    FROM dbo.fact_wait_stats
) z
WHERE prev IS NOT NULL;
GO

/* ---------------------------------------------------------------------------
   rpt.top_queries — top queries per snapshot, ranked by avg CPU and by avg duration
   so Power BI can show Top-N without heavy DAX.
   NOTE: Section 12 of CLAUDE.md names the source table "fact_query_store", but no such
   table exists in repo_schema.sql / the query_perf collector (Section 11) — this reads
   dbo.fact_query_perf, the table that actually exists. Flagged as a spec typo.
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW rpt.top_queries AS
SELECT
    source_instance, snapshot_time_utc, query_hash, exec_count, avg_cpu_ms, avg_duration_ms,
    avg_logical_reads, total_cpu_ms, query_sql_text,
    RANK() OVER (PARTITION BY source_instance, snapshot_time_utc ORDER BY avg_cpu_ms DESC)      AS cpu_rank,
    RANK() OVER (PARTITION BY source_instance, snapshot_time_utc ORDER BY avg_duration_ms DESC) AS duration_rank
FROM dbo.fact_query_perf;
GO

/* ---------------------------------------------------------------------------
   rpt.table_storage_latest — latest snapshot per table.
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW rpt.table_storage_latest AS
WITH latest AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY source_instance, database_name, schema_name, table_name
                               ORDER BY snapshot_date DESC) AS rn
    FROM dbo.fact_table_storage
)
SELECT source_instance, snapshot_date, database_name, schema_name, table_name,
       row_count, data_kb, index_kb, unused_kb
FROM latest
WHERE rn = 1;
GO

/* ---------------------------------------------------------------------------
   rpt.table_growth — latest snapshot vs. the closest snapshot >= 30 days earlier.
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW rpt.table_growth AS
SELECT
    cur.source_instance, cur.database_name, cur.schema_name, cur.table_name,
    cur.snapshot_date AS current_snapshot_date, cur.data_kb AS current_data_kb,
    prior.snapshot_date AS prior_snapshot_date, prior.data_kb AS prior_data_kb,
    cur.data_kb - prior.data_kb AS data_kb_growth,
    CASE WHEN prior.data_kb > 0
         THEN CAST(cur.data_kb - prior.data_kb AS DECIMAL(18, 2)) / prior.data_kb
         ELSE NULL
    END AS growth_pct
FROM rpt.table_storage_latest cur
OUTER APPLY (
    SELECT TOP (1) p.snapshot_date, p.data_kb
    FROM dbo.fact_table_storage p
    WHERE p.source_instance = cur.source_instance AND p.database_name = cur.database_name
      AND p.schema_name = cur.schema_name AND p.table_name = cur.table_name
      AND p.snapshot_date <= DATEADD(DAY, -30, cur.snapshot_date)
    ORDER BY p.snapshot_date DESC
) prior;
GO

/* ---------------------------------------------------------------------------
   rpt.table_access_daily — per-day access deltas from fact_table_usage, restart-safe
   (same LAG-with-floor pattern as rpt.wait_deltas). "Most accessed tables per day."
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW rpt.table_access_daily AS
SELECT source_instance, snapshot_date, database_name, schema_name, table_name,
       CASE WHEN scans_cum   >= p_scans   THEN scans_cum   - p_scans   ELSE scans_cum   END AS scans_day,
       CASE WHEN seeks_cum   >= p_seeks   THEN seeks_cum   - p_seeks   ELSE seeks_cum   END AS seeks_day,
       CASE WHEN lookups_cum >= p_lookups THEN lookups_cum - p_lookups ELSE lookups_cum END AS lookups_day,
       CASE WHEN updates_cum >= p_updates THEN updates_cum - p_updates ELSE updates_cum END AS writes_day,
       (CASE WHEN scans_cum   >= p_scans   THEN scans_cum   - p_scans   ELSE scans_cum   END)
     + (CASE WHEN seeks_cum   >= p_seeks   THEN seeks_cum   - p_seeks   ELSE seeks_cum   END)
     + (CASE WHEN lookups_cum >= p_lookups THEN lookups_cum - p_lookups ELSE lookups_cum END) AS reads_day
FROM (
    SELECT *,
        LAG(scans_cum)   OVER (PARTITION BY source_instance, database_name, schema_name, table_name
                                ORDER BY snapshot_date) AS p_scans,
        LAG(seeks_cum)   OVER (PARTITION BY source_instance, database_name, schema_name, table_name
                                ORDER BY snapshot_date) AS p_seeks,
        LAG(lookups_cum) OVER (PARTITION BY source_instance, database_name, schema_name, table_name
                                ORDER BY snapshot_date) AS p_lookups,
        LAG(updates_cum) OVER (PARTITION BY source_instance, database_name, schema_name, table_name
                                ORDER BY snapshot_date) AS p_updates
    FROM dbo.fact_table_usage
) z
WHERE p_scans IS NOT NULL;
GO

/* ---------------------------------------------------------------------------
   rpt.table_access_trend — rolling 90-day rollup of rpt.table_access_daily for the
   3-4 month access-pattern "dig". scan_ratio > 1 means scan-heavy (index candidate).
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW rpt.table_access_trend AS
SELECT
    source_instance, database_name, schema_name, table_name,
    AVG(CAST(reads_day  AS DECIMAL(18, 2))) AS avg_reads_per_day,
    AVG(CAST(scans_day  AS DECIMAL(18, 2))) AS avg_scans_per_day,
    AVG(CAST(writes_day AS DECIMAL(18, 2))) AS avg_writes_per_day,
    SUM(scans_day) AS total_scans,
    SUM(seeks_day) AS total_seeks,
    CASE WHEN SUM(seeks_day) > 0
         THEN CAST(SUM(scans_day) AS DECIMAL(18, 4)) / SUM(seeks_day)
         ELSE NULL
    END AS scan_ratio
FROM rpt.table_access_daily
WHERE snapshot_date >= DATEADD(DAY, -90, CAST(SYSUTCDATETIME() AS DATE))
GROUP BY source_instance, database_name, schema_name, table_name;
GO

/* ---------------------------------------------------------------------------
   rpt.index_opportunities — latest fact_index_ops snapshot per (instance, database).
   Missing-index candidates ranked by impact_score; unused candidates already satisfy
   reads = 0 AND writes > 0 by construction in index_ops.py.
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW rpt.index_opportunities AS
WITH latest_date AS (
    SELECT source_instance, database_name, MAX(snapshot_date) AS max_date
    FROM dbo.fact_index_ops
    GROUP BY source_instance, database_name
)
SELECT
    f.source_instance, f.snapshot_date, f.database_name, f.kind, f.object_name,
    f.impact_score, f.reads, f.writes, f.detail,
    CASE WHEN f.kind = 'missing'
         THEN RANK() OVER (PARTITION BY f.source_instance, f.database_name, f.kind
                            ORDER BY f.impact_score DESC)
         ELSE NULL
    END AS impact_rank
FROM dbo.fact_index_ops f
JOIN latest_date ld
    ON ld.source_instance = f.source_instance AND ld.database_name = f.database_name
   AND ld.max_date = f.snapshot_date;
GO

/* ---------------------------------------------------------------------------
   rpt.tuning_candidates — the analytical payoff: size x access pattern x index
   opportunities per table, with the contributing columns exposed so the dashboard
   can explain *why* each table is a candidate.
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW rpt.tuning_candidates AS
SELECT
    sz.source_instance, sz.database_name, sz.schema_name, sz.table_name,
    sz.data_kb, sz.row_count,
    tr.avg_reads_per_day, tr.avg_writes_per_day, tr.scan_ratio,
    io.kind AS index_issue_kind, io.object_name AS index_issue_object,
    io.impact_score, io.reads AS index_reads, io.writes AS index_writes, io.detail,
    CASE
        WHEN io.kind = 'missing'                                THEN 'add index'
        WHEN io.kind = 'unused'                                 THEN 'drop index'
        WHEN tr.avg_writes_per_day > 0 AND sz.data_kb > 1048576  THEN 'review fill factor / fragmentation'
        ELSE NULL
    END AS recommendation
FROM rpt.table_storage_latest sz
LEFT JOIN rpt.table_access_trend tr
    ON  tr.source_instance = sz.source_instance AND tr.database_name = sz.database_name
    AND tr.schema_name = sz.schema_name AND tr.table_name = sz.table_name
LEFT JOIN rpt.index_opportunities io
    ON  io.source_instance = sz.source_instance AND io.database_name = sz.database_name
    -- 'missing' rows: object_name = schema.table exactly; 'unused' rows: schema.table.index_name
    -- (a plain trailing-% LIKE would also match e.g. "dbo.Order" against table "Orders")
    AND (io.object_name = sz.schema_name + '.' + sz.table_name
         OR io.object_name LIKE sz.schema_name + '.' + sz.table_name + '.%')
WHERE sz.data_kb > 10240;  -- 10 MB+ tables only; smaller tables aren't worth tuning effort
GO

/* ---------------------------------------------------------------------------
   rpt.health_summary — latest fact_health per DB with RAG flags.
   Thresholds mirror config.yaml `thresholds:` (backup_full_max_age_hours: 24,
   backup_log_max_age_hours: 4) -- keep these two literals in sync with that file by hand,
   since this SQL view has no access to the Python-side YAML config.
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW rpt.health_summary AS
WITH latest_date AS (
    SELECT source_instance, database_name, MAX(snapshot_date) AS max_date
    FROM dbo.fact_health
    GROUP BY source_instance, database_name
)
SELECT
    h.source_instance, h.snapshot_date, h.database_name, h.recovery_model, h.state_desc,
    h.last_full_backup_utc, h.last_log_backup_utc, h.job_failures_24h,
    CASE
        WHEN h.state_desc <> 'ONLINE'                                                        THEN 'RED'
        WHEN h.job_failures_24h > 0                                                          THEN 'RED'
        WHEN h.last_full_backup_utc IS NULL
             OR DATEDIFF(HOUR, h.last_full_backup_utc, SYSUTCDATETIME()) > 24                THEN 'RED'
        WHEN h.recovery_model = 'FULL'
             AND (h.last_log_backup_utc IS NULL
                  OR DATEDIFF(HOUR, h.last_log_backup_utc, SYSUTCDATETIME()) > 4)            THEN 'AMBER'
        ELSE 'GREEN'
    END AS rag_status
FROM dbo.fact_health h
JOIN latest_date ld
    ON ld.source_instance = h.source_instance AND ld.database_name = h.database_name
   AND ld.max_date = h.snapshot_date;
GO

/* ---------------------------------------------------------------------------
   rpt.workload_by_category — fact_workload joined to dim_workload_class (first-match
   by priority). OUTER APPLY (not CROSS APPLY, unlike the Section 12 sketch) so rows
   with a NULL program_name (login had no client app recorded) still surface, tagged
   Unknown, instead of silently disappearing -- a LIKE match against NULL is never TRUE.
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW rpt.workload_by_category AS
SELECT
    f.source_instance, f.window_start_utc,
    ISNULL(c.workload_category, 'Unknown') AS workload_category,
    SUM(f.total_cpu_ms)         AS total_cpu_ms,
    SUM(f.total_logical_reads)  AS total_logical_reads,
    SUM(f.exec_count)           AS exec_count
FROM dbo.fact_workload f
OUTER APPLY (
    SELECT TOP (1) d.workload_category
    FROM dbo.dim_workload_class d
    WHERE f.program_name LIKE d.program_like
    ORDER BY d.priority
) c
GROUP BY f.source_instance, f.window_start_utc, ISNULL(c.workload_category, 'Unknown');
GO

/* ---------------------------------------------------------------------------
   rpt.top_logins — "best/worst users" by total CPU and total logical reads.
   --------------------------------------------------------------------------- */
CREATE OR ALTER VIEW rpt.top_logins AS
SELECT
    source_instance, login_name,
    SUM(total_cpu_ms)        AS total_cpu_ms,
    SUM(total_logical_reads) AS total_logical_reads,
    SUM(exec_count)          AS exec_count,
    RANK() OVER (PARTITION BY source_instance ORDER BY SUM(total_cpu_ms) DESC)        AS cpu_rank,
    RANK() OVER (PARTITION BY source_instance ORDER BY SUM(total_logical_reads) DESC) AS reads_rank
FROM dbo.fact_workload
GROUP BY source_instance, login_name;
GO

PRINT 'rpt.* reporting views deployed.';
GO
