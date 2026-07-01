/* ============================================================================
   SQL Server Fleet Observability — Repository Schema
   Target: the REPOSITORY instance (NOT the monitored instance).
   Deploy:  sqlcmd -S <repo_instance> -i repo_schema.sql
   Safe to re-run (idempotent guards on database, schema, and tables).
   ============================================================================ */

IF DB_ID('DBA_Observability') IS NULL
    CREATE DATABASE DBA_Observability;
GO

USE DBA_Observability;
GO

/* Reporting schema — Power BI reads ONLY objects in [rpt]. Views live in rpt_views.sql. */
IF SCHEMA_ID('rpt') IS NULL
    EXEC('CREATE SCHEMA rpt;');
GO

/* ---------------------------------------------------------------------------
   collection_run — every collector run logs here (guardrail #5)
   --------------------------------------------------------------------------- */
IF OBJECT_ID('dbo.collection_run') IS NULL
CREATE TABLE dbo.collection_run (
    run_id          BIGINT IDENTITY(1,1) PRIMARY KEY,
    source_instance SYSNAME       NOT NULL,
    task            VARCHAR(50)   NOT NULL,
    started_at_utc  DATETIME2     NOT NULL CONSTRAINT DF_run_started DEFAULT (SYSUTCDATETIME()),
    ended_at_utc    DATETIME2     NULL,
    status          VARCHAR(20)   NOT NULL,            -- running | success | failed
    row_count       INT           NULL,
    error_message   NVARCHAR(MAX) NULL
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_run_task_time')
    CREATE INDEX IX_run_task_time ON dbo.collection_run (task, started_at_utc DESC);
GO

/* ---------------------------------------------------------------------------
   fact_cpu — per-minute CPU history (backfilled from the scheduler ring buffer)
   --------------------------------------------------------------------------- */
IF OBJECT_ID('dbo.fact_cpu') IS NULL
CREATE TABLE dbo.fact_cpu (
    source_instance SYSNAME   NOT NULL,
    sample_time_utc DATETIME2 NOT NULL,
    sql_cpu_pct     TINYINT   NOT NULL,
    other_cpu_pct   TINYINT   NOT NULL,
    idle_pct        TINYINT   NOT NULL,
    CONSTRAINT PK_fact_cpu PRIMARY KEY (source_instance, sample_time_utc)
);
GO

/* ---------------------------------------------------------------------------
   fact_wait_stats — RAW CUMULATIVE snapshots; deltas are computed in rpt.wait_deltas
   --------------------------------------------------------------------------- */
IF OBJECT_ID('dbo.fact_wait_stats') IS NULL
CREATE TABLE dbo.fact_wait_stats (
    source_instance   SYSNAME      NOT NULL,
    snapshot_time_utc DATETIME2    NOT NULL,
    wait_type         NVARCHAR(60) NOT NULL,
    wait_ms_cum       BIGINT       NOT NULL,
    waiting_tasks_cum BIGINT       NOT NULL,
    CONSTRAINT PK_fact_wait PRIMARY KEY (source_instance, snapshot_time_utc, wait_type)
);
GO

/* ---------------------------------------------------------------------------
   fact_query_perf — top queries. On SQL 2019 without Query Store, sourced from the
   plan cache (sys.dm_exec_query_stats) at query_hash grain, one snapshot per run.
   If Query Store is later enabled, the collector can populate this from QS instead.
   Times stored in ms (DMV source is microseconds).
   --------------------------------------------------------------------------- */
IF OBJECT_ID('dbo.fact_query_perf') IS NULL
CREATE TABLE dbo.fact_query_perf (
    source_instance   SYSNAME       NOT NULL,
    snapshot_time_utc DATETIME2     NOT NULL,
    query_hash        BINARY(8)     NOT NULL,
    exec_count        BIGINT        NOT NULL,
    avg_cpu_ms        DECIMAL(18,2) NOT NULL,
    avg_duration_ms   DECIMAL(18,2) NOT NULL,
    avg_logical_reads DECIMAL(18,2) NOT NULL,
    total_cpu_ms      DECIMAL(18,2) NOT NULL,
    query_sql_text    NVARCHAR(MAX) NULL,
    CONSTRAINT PK_fact_query_perf PRIMARY KEY (source_instance, snapshot_time_utc, query_hash)
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_qperf_cpu')
    CREATE INDEX IX_qperf_cpu ON dbo.fact_query_perf (source_instance, snapshot_time_utc, avg_cpu_ms DESC);
GO

/* ---------------------------------------------------------------------------
   fact_table_storage — daily table volume snapshot
   --------------------------------------------------------------------------- */
IF OBJECT_ID('dbo.fact_table_storage') IS NULL
CREATE TABLE dbo.fact_table_storage (
    source_instance SYSNAME   NOT NULL,
    snapshot_date   DATE      NOT NULL,
    database_name   SYSNAME   NOT NULL,
    schema_name     SYSNAME   NOT NULL,
    table_name      SYSNAME   NOT NULL,
    row_count       BIGINT    NOT NULL,
    data_kb         BIGINT    NOT NULL,
    index_kb        BIGINT    NOT NULL,
    unused_kb       BIGINT    NOT NULL,
    CONSTRAINT PK_fact_storage PRIMARY KEY
        (source_instance, snapshot_date, database_name, schema_name, table_name)
);
GO

/* ---------------------------------------------------------------------------
   fact_index_ops — daily missing/unused index opportunities
   --------------------------------------------------------------------------- */
IF OBJECT_ID('dbo.fact_index_ops') IS NULL
CREATE TABLE dbo.fact_index_ops (
    source_instance SYSNAME       NOT NULL,
    snapshot_date   DATE          NOT NULL,
    database_name   SYSNAME       NOT NULL,
    kind            VARCHAR(20)   NOT NULL,            -- missing | unused
    object_name     NVARCHAR(400) NOT NULL,
    impact_score    DECIMAL(18,2) NULL,                -- missing-index impact
    reads           BIGINT        NULL,                -- unused: seeks+scans+lookups
    writes          BIGINT        NULL,                -- unused: user_updates
    detail          NVARCHAR(MAX) NULL,                -- column list / index name
    CONSTRAINT PK_fact_index PRIMARY KEY
        (source_instance, snapshot_date, database_name, kind, object_name)
);
GO

/* ---------------------------------------------------------------------------
   fact_table_usage — daily CUMULATIVE table access counts (rolled from index
   usage stats). Per-day deltas are computed in rpt.table_access_daily.
   Counters reset on restart; the delta view handles that.
   --------------------------------------------------------------------------- */
IF OBJECT_ID('dbo.fact_table_usage') IS NULL
CREATE TABLE dbo.fact_table_usage (
    source_instance      SYSNAME   NOT NULL,
    snapshot_date        DATE      NOT NULL,
    database_name        SYSNAME   NOT NULL,
    schema_name          SYSNAME   NOT NULL,
    table_name           SYSNAME   NOT NULL,
    seeks_cum            BIGINT    NOT NULL,
    scans_cum            BIGINT    NOT NULL,
    lookups_cum          BIGINT    NOT NULL,
    updates_cum          BIGINT    NOT NULL,
    last_user_read_utc   DATETIME2 NULL,
    last_user_update_utc DATETIME2 NULL,
    CONSTRAINT PK_fact_table_usage PRIMARY KEY
        (source_instance, snapshot_date, database_name, schema_name, table_name)
);
GO

/* ---------------------------------------------------------------------------
   fact_health — daily backup / recovery / state / job-failure snapshot
   --------------------------------------------------------------------------- */
IF OBJECT_ID('dbo.fact_health') IS NULL
CREATE TABLE dbo.fact_health (
    source_instance      SYSNAME     NOT NULL,
    snapshot_date        DATE        NOT NULL,
    database_name        SYSNAME     NOT NULL,
    last_full_backup_utc DATETIME2   NULL,
    last_log_backup_utc  DATETIME2   NULL,
    recovery_model       VARCHAR(20) NULL,
    state_desc           VARCHAR(30) NULL,
    job_failures_24h     INT         NULL,
    CONSTRAINT PK_fact_health PRIMARY KEY (source_instance, snapshot_date, database_name)
);
GO

/* ---------------------------------------------------------------------------
   fact_concurrency — near-real-time concurrency snapshot (1 row/minute, short
   retention). Powers the live concurrency TIMELINE; the instantaneous grid comes
   from the rt.* live views (realtime_queries.sql), not from here.
   --------------------------------------------------------------------------- */
IF OBJECT_ID('dbo.fact_concurrency') IS NULL
CREATE TABLE dbo.fact_concurrency (
    source_instance       SYSNAME   NOT NULL,
    sample_time_utc       DATETIME2 NOT NULL,
    user_sessions         INT       NOT NULL,
    running               INT       NOT NULL,
    runnable              INT       NOT NULL,   -- CPU pressure signal
    suspended             INT       NOT NULL,
    blocked               INT       NOT NULL,
    memory_grants_pending INT       NOT NULL,
    longest_open_tran_sec INT       NOT NULL,
    CONSTRAINT PK_fact_concurrency PRIMARY KEY (source_instance, sample_time_utc)
);
GO

PRINT 'DBA_Observability schema deployed.';
GO