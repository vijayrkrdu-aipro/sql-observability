/* ============================================================================
   SQL Server Fleet Observability — retention purge
   Target: the REPOSITORY instance. Run daily (its own scheduled task or a repo SQL
   Agent job) -- see README.md "Retention".

   Batched deletes (small DELETE TOP loops) avoid one giant transaction / lock escalation
   on tables that can accumulate a lot of rows (fact_concurrency at 1 row/min, fact_query_perf
   snapshots, etc). Retention windows mirror config.yaml `retention_days:` -- keep the two
   in sync by hand; this SQL file has no access to the Python-side YAML config.
   ============================================================================ */

USE DBA_Observability;
GO

DECLARE @BatchSize INT = 5000;

/* fact_cpu — 30 days */
WHILE 1 = 1
BEGIN
    DELETE TOP (@BatchSize) FROM dbo.fact_cpu
    WHERE sample_time_utc < DATEADD(DAY, -30, SYSUTCDATETIME());
    IF @@ROWCOUNT = 0 BREAK;
END
GO

DECLARE @BatchSize INT = 5000;

/* fact_wait_stats — 30 days */
WHILE 1 = 1
BEGIN
    DELETE TOP (@BatchSize) FROM dbo.fact_wait_stats
    WHERE snapshot_time_utc < DATEADD(DAY, -30, SYSUTCDATETIME());
    IF @@ROWCOUNT = 0 BREAK;
END
GO

DECLARE @BatchSize INT = 5000;

/* fact_query_perf — 120 days (raised to support the 3-4 month access-pattern analysis) */
WHILE 1 = 1
BEGIN
    DELETE TOP (@BatchSize) FROM dbo.fact_query_perf
    WHERE snapshot_time_utc < DATEADD(DAY, -120, SYSUTCDATETIME());
    IF @@ROWCOUNT = 0 BREAK;
END
GO

DECLARE @BatchSize INT = 5000;

/* fact_workload — 120 days (keep attribution history for access-pattern analysis) */
WHILE 1 = 1
BEGIN
    DELETE TOP (@BatchSize) FROM dbo.fact_workload
    WHERE window_start_utc < DATEADD(DAY, -120, SYSUTCDATETIME());
    IF @@ROWCOUNT = 0 BREAK;
END
GO

DECLARE @BatchSize INT = 5000;

/* fact_session_sample — 14 days (optional zero-DDL fallback collector) */
WHILE 1 = 1
BEGIN
    DELETE TOP (@BatchSize) FROM dbo.fact_session_sample
    WHERE sample_time_utc < DATEADD(DAY, -14, SYSUTCDATETIME());
    IF @@ROWCOUNT = 0 BREAK;
END
GO

DECLARE @BatchSize INT = 5000;

/* fact_concurrency — 7 days (near-real-time, 1 row/min -> high volume, short retention) */
WHILE 1 = 1
BEGIN
    DELETE TOP (@BatchSize) FROM dbo.fact_concurrency
    WHERE sample_time_utc < DATEADD(DAY, -7, SYSUTCDATETIME());
    IF @@ROWCOUNT = 0 BREAK;
END
GO

DECLARE @BatchSize INT = 5000;

/* fact_table_storage — 365 days */
WHILE 1 = 1
BEGIN
    DELETE TOP (@BatchSize) FROM dbo.fact_table_storage
    WHERE snapshot_date < DATEADD(DAY, -365, CAST(SYSUTCDATETIME() AS DATE));
    IF @@ROWCOUNT = 0 BREAK;
END
GO

DECLARE @BatchSize INT = 5000;

/* fact_table_usage — 365 days (long horizon for access-pattern / tuning analysis) */
WHILE 1 = 1
BEGIN
    DELETE TOP (@BatchSize) FROM dbo.fact_table_usage
    WHERE snapshot_date < DATEADD(DAY, -365, CAST(SYSUTCDATETIME() AS DATE));
    IF @@ROWCOUNT = 0 BREAK;
END
GO

DECLARE @BatchSize INT = 5000;

/* fact_index_ops — 365 days */
WHILE 1 = 1
BEGIN
    DELETE TOP (@BatchSize) FROM dbo.fact_index_ops
    WHERE snapshot_date < DATEADD(DAY, -365, CAST(SYSUTCDATETIME() AS DATE));
    IF @@ROWCOUNT = 0 BREAK;
END
GO

DECLARE @BatchSize INT = 5000;

/* fact_health — 365 days */
WHILE 1 = 1
BEGIN
    DELETE TOP (@BatchSize) FROM dbo.fact_health
    WHERE snapshot_date < DATEADD(DAY, -365, CAST(SYSUTCDATETIME() AS DATE));
    IF @@ROWCOUNT = 0 BREAK;
END
GO

DECLARE @BatchSize INT = 5000;

/* collection_run — 90 days */
WHILE 1 = 1
BEGIN
    DELETE TOP (@BatchSize) FROM dbo.collection_run
    WHERE started_at_utc < DATEADD(DAY, -90, SYSUTCDATETIME());
    IF @@ROWCOUNT = 0 BREAK;
END
GO

DECLARE @BatchSize INT = 5000;

/* fact_io_latency — 30 days (Phase 4) */
WHILE 1 = 1
BEGIN
    DELETE TOP (@BatchSize) FROM dbo.fact_io_latency
    WHERE snapshot_time_utc < DATEADD(DAY, -30, SYSUTCDATETIME());
    IF @@ROWCOUNT = 0 BREAK;
END
GO

DECLARE @BatchSize INT = 5000;

/* fact_blocking_snapshot — 7 days (Phase 4, near-real-time) */
WHILE 1 = 1
BEGIN
    DELETE TOP (@BatchSize) FROM dbo.fact_blocking_snapshot
    WHERE sample_time_utc < DATEADD(DAY, -7, SYSUTCDATETIME());
    IF @@ROWCOUNT = 0 BREAK;
END
GO

DECLARE @BatchSize INT = 5000;

/* fact_deadlock — 180 days (Phase 4; rare + high-signal, keep a long trend window) */
WHILE 1 = 1
BEGIN
    DELETE TOP (@BatchSize) FROM dbo.fact_deadlock
    WHERE event_time_utc < DATEADD(DAY, -180, SYSUTCDATETIME());
    IF @@ROWCOUNT = 0 BREAK;
END
GO

PRINT 'Retention purge complete.';
GO
