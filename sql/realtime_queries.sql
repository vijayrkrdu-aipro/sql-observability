/* ============================================================================
   REAL-TIME / LIVE queries — concurrency + query performance (SQL Server 2019)
   ----------------------------------------------------------------------------
   These are LIVE, point-in-time DMV queries. They persist nothing. Use them:
     (a) ad-hoc in SSMS,  or
     (b) as Power BI DirectQuery sources (native query) with Automatic Page Refresh,
   No deployment required for (a)/(b). Optionally deploy the CREATE VIEW wrappers
   below on the MONITORED instance (one-time, read-only views) for convenience.

   All are read-only and need only VIEW SERVER STATE. Nothing here writes or alters.
   For deep blocking-chain / historical deadlock analysis, sp_WhoIsActive and the
   system_health XE session remain the best interactive tools.
   ============================================================================ */

/* 1. ACTIVE REQUESTS RIGHT NOW — the "what's running" grid, most expensive first.
      Query-perf + concurrency core. */
CREATE OR ALTER VIEW rt.active_requests AS
SELECT
    r.session_id,
    s.login_name, s.host_name, s.program_name,
    DB_NAME(r.database_id)            AS database_name,
    r.status,                                   -- running | runnable | suspended
    r.command, r.wait_type, r.wait_time AS wait_time_ms,
    r.blocking_session_id,
    r.cpu_time                        AS cpu_ms,
    r.total_elapsed_time              AS elapsed_ms,
    r.logical_reads, r.writes,
    r.granted_query_memory * 8        AS granted_mem_kb,
    r.percent_complete,
    SUBSTRING(t.text, (r.statement_start_offset/2)+1,
        ((CASE r.statement_end_offset WHEN -1 THEN DATALENGTH(t.text)
          ELSE r.statement_end_offset END - r.statement_start_offset)/2)+1) AS running_sql
FROM sys.dm_exec_requests r
JOIN sys.dm_exec_sessions s ON r.session_id = s.session_id
OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) t
WHERE s.is_user_process = 1 AND r.session_id <> @@SPID;
GO

/* 2. BLOCKING — who is blocking whom, right now. */
CREATE OR ALTER VIEW rt.blocking AS
SELECT
    w.blocking_session_id AS blocker_spid,
    w.session_id          AS blocked_spid,
    w.wait_type, w.wait_duration_ms, w.resource_description,
    bs.login_name AS blocker_login, bs.program_name AS blocker_program, bs.host_name AS blocker_host,
    ws.login_name AS blocked_login, ws.program_name AS blocked_program
FROM sys.dm_os_waiting_tasks w
LEFT JOIN sys.dm_exec_sessions bs ON w.blocking_session_id = bs.session_id
LEFT JOIN sys.dm_exec_sessions ws ON w.session_id          = ws.session_id
WHERE w.blocking_session_id IS NOT NULL
  AND w.blocking_session_id <> w.session_id;
GO

/* 3. LIVE WAITS — what sessions are waiting on at this moment (vs. cumulative history). */
CREATE OR ALTER VIEW rt.live_waits AS
SELECT wait_type,
       COUNT(*)               AS waiting_tasks,
       SUM(wait_duration_ms)  AS total_wait_ms
FROM sys.dm_os_waiting_tasks
WHERE session_id > 50
GROUP BY wait_type;
GO

/* 4. CONCURRENCY SNAPSHOT — one-row health of concurrency + CPU/memory pressure now.
      (This is also what the `concurrency` collector persists for a live timeline.) */
CREATE OR ALTER VIEW rt.concurrency_now AS
SELECT
    (SELECT COUNT(*) FROM sys.dm_exec_sessions WHERE is_user_process = 1)              AS user_sessions,
    (SELECT COUNT(*) FROM sys.dm_exec_requests WHERE status = 'running')               AS running,
    (SELECT COUNT(*) FROM sys.dm_exec_requests WHERE status = 'runnable')              AS runnable_cpu_pressure,
    (SELECT COUNT(*) FROM sys.dm_exec_requests WHERE status = 'suspended')             AS suspended_waiting,
    (SELECT COUNT(*) FROM sys.dm_exec_requests WHERE blocking_session_id <> 0)         AS blocked,
    (SELECT COUNT(*) FROM sys.dm_exec_query_memory_grants WHERE grant_time IS NULL)    AS memory_grants_pending,
    (SELECT ISNULL(MAX(DATEDIFF(SECOND, at.transaction_begin_time, SYSDATETIME())), 0)
       FROM sys.dm_tran_active_transactions at)                                        AS longest_open_tran_sec;
GO

/* 5. MEMORY GRANTS — pending/large grants (RESOURCE_SEMAPHORE pressure). */
CREATE OR ALTER VIEW rt.memory_grants AS
SELECT session_id, requested_memory_kb, granted_memory_kb, grant_time,
       queue_id, wait_time_ms, dop
FROM sys.dm_exec_query_memory_grants;
GO

/* 6. TEMPDB BY SESSION — who is consuming tempdb now (spills / concurrency). */
CREATE OR ALTER VIEW rt.tempdb_by_session AS
SELECT ssu.session_id, s.login_name, s.program_name,
       (ssu.user_objects_alloc_page_count + ssu.internal_objects_alloc_page_count) * 8 AS tempdb_alloc_kb
FROM sys.dm_db_session_space_usage ssu
JOIN sys.dm_exec_sessions s ON ssu.session_id = s.session_id
WHERE (ssu.user_objects_alloc_page_count + ssu.internal_objects_alloc_page_count) > 0;
GO

/* 7. LONG-RUNNING OPEN TRANSACTIONS — blocking / log-growth risk. */
CREATE OR ALTER VIEW rt.long_transactions AS
SELECT st.session_id, s.login_name, s.program_name,
       at.name AS tran_name, at.transaction_begin_time,
       DATEDIFF(SECOND, at.transaction_begin_time, SYSDATETIME()) AS open_seconds
FROM sys.dm_tran_active_transactions at
JOIN sys.dm_tran_session_transactions st ON at.transaction_id = st.transaction_id
JOIN sys.dm_exec_sessions s              ON st.session_id      = s.session_id;
GO

/* 8. SCHEDULER / CPU PRESSURE NOW — runnable_tasks_count > 0 sustained = CPU queueing. */
CREATE OR ALTER VIEW rt.cpu_schedulers AS
SELECT scheduler_id, current_tasks_count, runnable_tasks_count, work_queue_count, pending_disk_io_count
FROM sys.dm_os_schedulers
WHERE scheduler_id < 255;
GO

/* --------------------------------------------------------------------------
   NOTE ON DEPLOYMENT:
   The CREATE VIEW statements above require a one-time CREATE SCHEMA rt; on the
   MONITORED instance and CREATE VIEW permission (read-only views, in change
   control). If you prefer ZERO changes on the monitored instance, drop the
   "CREATE OR ALTER VIEW rt.x AS" line from each and run/embed the SELECT directly
   (SSMS or Power BI DirectQuery native query).
   -------------------------------------------------------------------------- */
