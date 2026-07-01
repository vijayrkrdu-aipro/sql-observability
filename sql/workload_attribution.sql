/* ============================================================================
   Workload & Login Attribution module
   ----------------------------------------------------------------------------
   PART A (repository instance): dim + fact tables. Re-runnable.
   PART B (MONITORED instance, ONE-TIME, by a DBA): Extended Events session.
          Requires ALTER ANY EVENT SESSION. The runtime collector only READS it.
   ============================================================================ */

/* =======================  PART A — on the REPOSITORY  ====================== */
USE DBA_Observability;
GO

/* Classification lookup — edit freely; lower priority wins (first match). */
IF OBJECT_ID('dbo.dim_workload_class') IS NULL
CREATE TABLE dbo.dim_workload_class (
    class_id          INT          NOT NULL PRIMARY KEY,
    priority          INT          NOT NULL,            -- evaluated ascending; first match wins
    program_like      NVARCHAR(256) NOT NULL,           -- LIKE pattern against client_app_name
    workload_category VARCHAR(50)  NOT NULL,
    notes             NVARCHAR(200) NULL
);
GO

IF NOT EXISTS (SELECT 1 FROM dbo.dim_workload_class)
INSERT INTO dbo.dim_workload_class (class_id, priority, program_like, workload_category, notes) VALUES
 (1 , 10, N'%Integration Services%', 'ETL/SSIS',            NULL),
 (2 , 11, N'SSIS-%',                 'ETL/SSIS',            NULL),
 (3 , 20, N'%Tableau%',              'BI/Tableau',          NULL),
 (4 , 21, N'%Power BI%',             'BI/Power BI',         NULL),
 (5 , 22, N'%MSOLAP%',               'BI/Analysis Services',NULL),
 (6 , 30, N'%Report Server%',        'Reporting/SSRS',      NULL),
 (7 , 31, N'%ReportingServices%',    'Reporting/SSRS',      NULL),
 (8 , 40, N'%Azure Data Factory%',   'ETL/ADF',             NULL),
 (9 , 41, N'%azure-sql-%',           'ETL/ADF',             NULL),
 (10, 50, N'%Management Studio%',    'Admin/SSMS',          N'interactive / ad-hoc'),
 (11, 51, N'SQLCMD%',                'Admin/CLI',           NULL),
 (12, 52, N'%azdata%',               'Admin/CLI',           NULL),
 (13, 60, N'%.Net SqlClient%',       'App (generic)',       N'set Application Name= to refine'),
 (14, 61, N'%ODBC%',                 'App/Extract (ODBC)',  N'often ad-hoc extracts'),
 (15, 62, N'%jdbc%',                 'App/Extract (JDBC)',  NULL),
 (99, 999, N'%',                     'Unknown',             N'catch-all');
GO

/* Accurate per-completed-query attribution, aggregated per collection window.
   attribution_hash keeps the natural key bounded for MERGE upserts. */
IF OBJECT_ID('dbo.fact_workload') IS NULL
CREATE TABLE dbo.fact_workload (
    source_instance    SYSNAME       NOT NULL,
    window_start_utc   DATETIME2     NOT NULL,
    login_name         SYSNAME       NULL,
    program_name       NVARCHAR(256) NULL,
    host_name          NVARCHAR(128) NULL,
    database_name      SYSNAME       NULL,
    exec_count         BIGINT        NOT NULL,
    total_cpu_ms       DECIMAL(18,2) NOT NULL,
    total_duration_ms  DECIMAL(18,2) NOT NULL,
    total_logical_reads BIGINT       NOT NULL,
    total_writes       BIGINT        NOT NULL,
    total_rows         BIGINT        NOT NULL,
    attribution_hash AS CONVERT(BINARY(32), HASHBYTES('SHA2_256',
        CONCAT(login_name,'|',program_name,'|',host_name,'|',database_name))) PERSISTED,
    CONSTRAINT PK_fact_workload PRIMARY KEY (source_instance, window_start_utc, attribution_hash)
);
GO

/* OPTIONAL fallback (zero-DDL on monitored instance): point-in-time active-request sampler. */
IF OBJECT_ID('dbo.fact_session_sample') IS NULL
CREATE TABLE dbo.fact_session_sample (
    source_instance SYSNAME       NOT NULL,
    sample_time_utc DATETIME2     NOT NULL,
    login_name      SYSNAME       NULL,
    program_name    NVARCHAR(256) NULL,
    host_name       NVARCHAR(128) NULL,
    database_name   SYSNAME       NULL,
    active_requests INT           NOT NULL,    -- concurrent active requests at sample time
    cpu_ms_inflight BIGINT        NULL,        -- sum of in-flight request cpu_time (ms)
    reads_inflight  BIGINT        NULL,
    CONSTRAINT PK_fact_session_sample PRIMARY KEY
        (source_instance, sample_time_utc, login_name, program_name, host_name, database_name)
);
GO
PRINT 'Workload attribution tables ready.';
GO

/* =================  PART B — on the MONITORED instance (ONE-TIME)  =========
   Run separately, by a DBA, in change control. Requires ALTER ANY EVENT SESSION.
   Set @filepath to a real, SQL-Server-writable folder. The collector reads these
   .xel files via sys.fn_xe_file_target_read_file (needs only VIEW SERVER STATE).

   duration / cpu_time are MICROSECONDS. The WHERE filters to queries > 1s so the
   session stays cheap; lower it only if you need finer attribution.

CREATE EVENT SESSION [Observability_Workload] ON SERVER
ADD EVENT sqlserver.rpc_completed (
    ACTION (sqlserver.server_principal_name, sqlserver.client_app_name,
            sqlserver.client_hostname, sqlserver.database_name)
    WHERE  (duration > 1000000)
),
ADD EVENT sqlserver.sql_batch_completed (
    ACTION (sqlserver.server_principal_name, sqlserver.client_app_name,
            sqlserver.client_hostname, sqlserver.database_name)
    WHERE  (duration > 1000000)
)
ADD TARGET package0.event_file (
    SET filename = N'<WRITABLE_FOLDER>\Observability_Workload.xel',
        max_file_size = 256,          -- MB per file
        max_rollover_files = 5
)
WITH (MAX_DISPATCH_LATENCY = 30 SECONDS, STARTUP_STATE = ON);

ALTER EVENT SESSION [Observability_Workload] ON SERVER STATE = START;
============================================================================ */
