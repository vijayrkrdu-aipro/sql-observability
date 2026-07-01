# CLAUDE.md — SQL Server Fleet Observability (Offline Build / Code-Only)

> **How to use:** Place at repo root, open in Claude Code. To start:
> *"Read CLAUDE.md and begin Phase 1, Task 1.1."*
> Detailed for the build; once it's complete, trim sections 10–13 into `.claude/rules/` to keep
> session context lean (long CLAUDE.md files reduce instruction adherence).

---

## 1. Build environment & operating mode — READ FIRST

This repository is being built **outside the organization's network**, on a machine with **no access
to any SQL Server**. The code will be **ported inside the org later** and run there.

**Therefore, in this environment Claude Code MUST:**
- **Never attempt to connect to a database.** There is none here. No live queries, no agentic runs
  against SQL Server, no "let me just test the connection."
- Treat the deliverable as **code + SQL + tests, committed and pushed to git** (Section 9).
- Verify everything **offline only**: Python compiles, `ruff` is clean, `pytest` passes against a
  **mocked** database layer, SQL files parse (best-effort). See Section 13.
- Author every SQL query **correct-by-specification** (Sections 11–12), because there is no live
  instance to catch mistakes. Live validation is the human's job at port time (Section 14).

If a task seems to require a live database, **stop and write a mocked test instead**, then note it
for the porting checklist.

## 2. Project overview & goal

A lightweight **Python** observability platform for SQL Server. Read-only collectors pull metrics
from a monitored instance, write them to a **separate repository instance**, and expose a stable
`rpt.*` view layer that **Power BI** renders as a **management dashboard**. No agent software, no GUI,
no third-party monitoring tool.

**Scope now:** one monitored instance, designed so 1→N is config-only. **Audience:** management —
dashboard only; green/amber/red and monthly trends, not per-minute noise.

**Target environment: SQL Server 2019**, and **Query Store is NOT enabled.** So top-query metrics come
from the **plan-cache DMVs** (`sys.dm_exec_query_stats`), not Query Store — no database changes needed.
Every other source (ring-buffer CPU, wait stats, storage/index/usage DMVs, Extended Events for
attribution) is natively available on 2019 and unchanged. Enabling Query Store later is an optional
upgrade (Section 16), not a requirement.

## 3. Architecture

```
Monitored SQL ──read-only──► Python collectors ──► Repository SQL (DBA_Observability)
                                                         │
                                                    rpt.* views ──► Gateway ──► Power BI
```
Collectors are scheduler-agnostic CLI jobs invoked by an external scheduler (Control-M / cron / Task
Scheduler) **inside the org**.

### Historic vs. real-time (two layers)

- **Historic** (persisted): all collectors below write to the repo; Power BI reads `rpt.*` in **Import**
  mode. This is the bulk of the platform.
- **Real-time** (live): the `rt.*` views (`sql/realtime_queries.sql`) are point-in-time DMV queries —
  they **persist nothing**. Surface them via Power BI **DirectQuery + Automatic Page Refresh** (or run
  ad-hoc in SSMS) for live query-performance and concurrency. The one exception that bridges both is
  the lightweight `concurrency` collector, which snapshots concurrency counts every minute into
  `fact_concurrency` so you also get a live *timeline* (not just an instantaneous grid).

### Collection cadence (reference — the scheduler owns cadence, not the code)

| Task | Cadence | Why |
|---|---|---|
| `cpu` | 15 min | ring buffer holds ~256 min; 15 min gives margin against restarts |
| `waits` | 15 min | finer windows = better "why slow" resolution |
| `query_perf` | 30 min | plan cache is volatile — sample often to catch heavy hitters before eviction |
| `workload` | 30 min | drains accumulated XE events; XE captures continuously regardless |
| `concurrency` | 1 min | near-real-time timeline; short retention |
| `storage`, `index_ops`, `table_access`, `health` | daily | slow-moving; run `table_access` at a fixed time so deltas align |
| `rt.*` live views | on demand / DirectQuery APR every 15–60 s | not collected — queried live |

## 4. Tech stack

- **Python 3.11+**, `venv`.
- **pyodbc** + **ODBC Driver 18 for SQL Server** — *runtime dependency, used only inside the org.*
  It may not even be installed on the build machine; that's fine — the DB layer is import-isolated
  and mocked in tests so the suite runs without it.
- **PyYAML** (config), **python-dotenv** (optional local creds).
- **pytest** (mocked unit tests), **ruff** (lint).
- *(Optional)* **sqlglot** for a best-effort T-SQL parse check of `sql/*.sql`.
- **Two dependency files:** `requirements.txt` = runtime for the enterprise host (`pyodbc`, `PyYAML`,
  `python-dotenv`); `requirements-dev.txt` = the build/test machine (`pytest`, `ruff`, `sqlglot`,
  `PyYAML`) — **no `pyodbc`**, since the DB layer is import-isolated and mocked. On the build machine
  install only `requirements-dev.txt`.

## 5. Repository structure

```
sql-observability/
├── CLAUDE.md  README.md  requirements.txt  requirements-dev.txt  config.yaml  .env.example  .gitignore
│                          # ^ all PROVIDED as starter files (build src/ + remaining sql/ from here)
├── sql/
│   ├── repo_schema.sql        # provided — fact tables + collection_run
│   ├── workload_attribution.sql # provided — dim + workload facts (PART A) + XE session (PART B)
│   ├── realtime_queries.sql   # provided — rt.* live views (real-time layer; not built by Claude Code)
│   ├── rpt_views.sql          # BUILD in Phase 2
│   └── retention.sql          # BUILD in Phase 2
├── src/
│   ├── db.py                  # connection factory + run-logging (import-isolated)
│   ├── config.py              # load + validate config.yaml
│   └── collectors/
│       ├── base.py  cpu.py  waits.py  query_perf.py  storage.py  index_ops.py  health.py
│       ├── table_access.py    # daily table access counts (most-accessed-per-day, patterns)
│       ├── concurrency.py     # near-real-time concurrency timeline (1-min snapshot)
│       ├── workload.py        # XE reader — login + workload-type attribution
│       └── sessions.py        # optional DMV sampler (zero-DDL fallback)
├── run.py                     # CLI: python run.py --task <name> [--dry-run]
├── tests/
│   ├── conftest.py            # FakeConnection / FakeCursor fixtures (Section 13)
│   ├── fixtures/              # canned DMV rowsets as JSON
│   └── test_*.py
└── .github/workflows/ci.yml   # optional: ruff + pytest on push (no DB)
```

## 6. Hard guardrails (never violate)

1. **No DB connection in this environment** (Section 1).
2. **Collectors are READ-ONLY on monitored instances** by design — only `SELECT` from DMVs, Extended
   Events targets, and `msdb`. All writes target the **repository** instance only.
3. **Assume only `VIEW SERVER STATE`** (+ `VIEW DATABASE STATE`, `msdb` read) on monitored instances.
4. **No secrets in code, config.yaml, or git.** Prefer Windows Integrated auth. Credentials, if any,
   come from environment variables. Never log connection strings or credentials.
5. **Idempotent**: re-running a task for the same window must not duplicate rows (upsert on natural key).
6. **`source_instance` on every fact row.** 1→N is config, never a schema change.
7. **Power BI reads only `rpt.*`.**

## 7. Design for testability (so it works without a DB)

`src/db.py` exposes a **connection factory** and a thin `execute(query, params) -> rows` helper.
Collectors receive their connection via injection — they never import pyodbc directly. Tests pass a
`FakeConnection` that returns canned fixture rows. This is the mechanism that lets the whole suite run
on a machine with no SQL Server and no ODBC driver.

## 8. Conventions

- **CLI:** `python run.py --task <name> [--dry-run] [--config config.yaml]`. `--dry-run` runs the
  transform and prints rowcounts but performs **no writes** (and needs no DB — it can run against a
  fake in tests). Exit `0` success / non-zero failure.
- **Collector contract** (`base.Collector`): `source_query()` returns SQL; `transform(rows)` shapes
  repo rows; `upsert_sql()` returns the MERGE/insert; `base` handles `collection_run` logging, timing,
  dry-run, and error capture.
- **Naming:** Python `snake_case`; SQL `fact_*` / `collection_run` / `rpt.*`.
- **Time:** store UTC; convert in Power BI.
- **Errors:** catch per task, log full traceback, mark the run `failed`, exit non-zero. One failing
  task never blocks another.

## 9. Git workflow (the deliverable)

Claude Code **may run git** (`add`, `commit`, `push`) but must **not** create remotes or touch
credentials — the human configures the remote and auth.

- **Branching:** one branch per phase, e.g. `feat/phase1-collectors`. Merge to `main` at phase end
  (fast-forward or PR — human's choice).
- **Commit per completed task**, only after that task's offline acceptance criteria pass.
- **Conventional commits:** `feat:`, `fix:`, `test:`, `docs:`, `chore:`. Example:
  `feat(collectors): add cpu ring-buffer collector with mocked test`.
- **Never commit** `.env`, logs, venv, `__pycache__` (`.gitignore` covers these). Verify `git status`
  is clean of secrets before every commit.
- **Push** the phase branch after each task so work is never lost.

## 10. Repository schema

Already provided in `sql/repo_schema.sql` (deploy happens at port time, not here). Tables:
`collection_run`, `fact_cpu`, `fact_wait_stats`, `fact_query_perf`, `fact_table_storage`,
`fact_index_ops`, `fact_table_usage`, `fact_health`, `fact_concurrency`, plus the `rpt` schema. Build
all code against these exact columns.

The **workload/login attribution** module adds `dim_workload_class`, `fact_workload`, and
`fact_session_sample` (see `sql/workload_attribution.sql`). Its Extended Events session is deployed
**one-time on the monitored instance by a DBA** (PART B of that file); the collector only reads it.

The **real-time** module (`sql/realtime_queries.sql`) provides the `rt.*` live views — provided, not
built by Claude Code (see Section 11).

## 11. Collector specifications (author correct-by-spec)

The two tricky ones are given in full T-SQL — they are the easiest to get wrong with no live DB.

**cpu.py** — run against the monitored instance (master context). Returns ~256 minutes of per-minute
history per poll; upsert by `sample_time_utc` (idempotent).
```sql
DECLARE @now BIGINT = (SELECT ms_ticks FROM sys.dm_os_sys_info);
SELECT
    DATEADD(ms, -1 * (@now - rb.[timestamp]), SYSUTCDATETIME())                                  AS sample_time_utc,
    r.value('(./Record/SchedulerMonitorEvent/SystemHealth/ProcessUtilization)[1]','tinyint')    AS sql_cpu_pct,
    r.value('(./Record/SchedulerMonitorEvent/SystemHealth/SystemIdle)[1]','tinyint')            AS idle_pct
FROM (
    SELECT [timestamp], CONVERT(xml, record) AS r
    FROM sys.dm_os_ring_buffers
    WHERE ring_buffer_type = N'RING_BUFFER_SCHEDULER_MONITOR'
      AND record LIKE '%<SystemHealth>%'
) rb;
-- other_cpu_pct = 100 - sql_cpu_pct - idle_pct  (compute in transform; clamp to >= 0)
```

**query_perf.py** — top queries **without Query Store** (SQL 2019). Source = the plan cache via
`sys.dm_exec_query_stats` + `sys.dm_exec_sql_text`, aggregated to **`query_hash` grain** (so the same
query shape with different literals collapses to one row and is trendable across snapshots). Capture
**top-N by CPU and top-N by logical reads** (union on `query_hash`; `top_n` from config). **Times are
microseconds — divide by 1000 for ms.** Store one snapshot per run stamped with `snapshot_time_utc`;
upsert on `(source_instance, snapshot_time_utc, query_hash)`. Handle NULL text (plan may be evicted).
```sql
SELECT TOP (@top_n)
    qs.query_hash,
    SUM(qs.execution_count)                                          AS exec_count,
    SUM(qs.total_worker_time)  / 1000.0                             AS total_cpu_ms,        -- µs -> ms
    SUM(qs.total_worker_time)  / NULLIF(SUM(qs.execution_count),0) / 1000.0 AS avg_cpu_ms,
    SUM(qs.total_elapsed_time) / NULLIF(SUM(qs.execution_count),0) / 1000.0 AS avg_duration_ms,
    SUM(qs.total_logical_reads)/ NULLIF(SUM(qs.execution_count),0)          AS avg_logical_reads,
    MIN(SUBSTRING(st.text, (qs.statement_start_offset/2)+1,
        ((CASE qs.statement_end_offset WHEN -1 THEN DATALENGTH(st.text)
          ELSE qs.statement_end_offset END - qs.statement_start_offset)/2)+1)) AS query_sql_text
FROM sys.dm_exec_query_stats qs
CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) st
GROUP BY qs.query_hash
ORDER BY SUM(qs.total_worker_time) DESC;   -- second pull: ORDER BY SUM(total_logical_reads) DESC
```
*Caveat to know:* the plan cache is **cumulative-since-compile and volatile** (evicts on memory
pressure, recompiles, restart), so this is a periodic *snapshot* of current top consumers, not
complete history. It captures heavy hitters well. If Query Store is later enabled, set
`query_perf.source: query_store` in config and the collector instead reads
`sys.query_store_runtime_stats` (per-database; skip DBs where `actual_state_desc <> 'READ_WRITE'`;
values also microseconds) — giving true time-bucketed history. Same `fact_query_perf` shape either way.

**waits.py** — `SELECT wait_type, wait_time_ms, waiting_tasks_count FROM sys.dm_os_wait_stats`. Store
**raw cumulative** with one `snapshot_time_utc`; deltas are computed in `rpt.wait_deltas`. Exclude the
benign wait types listed in `config.yaml`.

**storage.py** — per database, from `sys.dm_db_partition_stats ps` joined to `sys.tables`/`sys.schemas`
(and `sys.indexes` for index_id). `reserved_page_count*8`=reserved KB, `used_page_count*8`=used,
`(in_row_data_page_count + lob_used_page_count + row_overflow_used_page_count)*8`=data KB;
`index_kb = used - data`, `unused_kb = reserved - used`; `row_count` from `index_id IN (0,1)`. One row
per table per day.

**index_ops.py** — two pulls:
- *missing:* `sys.dm_db_missing_index_details` + `_groups` + `_group_stats`;
  `impact_score = avg_total_user_cost * avg_user_impact * (user_seeks + user_scans)`; `detail` =
  equality/inequality/included columns.
- *unused:* `sys.dm_db_index_usage_stats` (this DB; `database_id = DB_ID()`);
  `reads = user_seeks+user_scans+user_lookups`, `writes = user_updates`; candidate when
  `reads = 0 AND writes > 0`. (These counters reset on restart — annotate, do not delta.)

**table_access.py** — *most-accessed-tables-per-day + access patterns.* Per database, aggregate
`sys.dm_db_index_usage_stats` (`database_id = DB_ID()`) to **table grain** — join to `sys.objects o`
(`o.type = 'U' AND o.is_ms_shipped = 0`) and `sys.schemas`, group by table summing across all indexes:
`SUM(user_seeks)`→seeks_cum, `SUM(user_scans)`→scans_cum, `SUM(user_lookups)`→lookups_cum,
`SUM(user_updates)`→updates_cum, `MAX(last_user_seek/scan/lookup)`→last_user_read_utc,
`MAX(last_user_update)`→last_user_update_utc. Store the **cumulative** values as one row per table per
day in `fact_table_usage` (per-day deltas come from `rpt.table_access_daily`). Run at a **consistent
daily time** so consecutive snapshots delta cleanly. Notes: counters reset on restart (delta view
handles it); a table absent from the DMV simply hasn't been touched since restart — treat as
not-collected, not zero. Shares its DMV with `index_ops` — a single read could feed both later, but
keep them separate for clarity now. Cadence: daily.

**health.py** — latest full/log backup per DB from `msdb.dbo.backupset`; `recovery_model_desc` and
`state_desc` from `sys.databases`; `job_failures_24h` from `msdb.dbo.sysjobhistory`/`sysjobs`
(`run_status = 0` in last 24h). One row per DB per day.

**workload.py** — *login + workload-type attribution (the headline feature).* Reads the Extended
Events session `Observability_Workload` (created one-time by a DBA — see `sql/workload_attribution.sql`
PART B; the collector NEVER creates it). Read-only at runtime via
`sys.fn_xe_file_target_read_file(N'...Observability_Workload*.xel', NULL, NULL, NULL)` (needs only
`VIEW SERVER STATE`). For each event parse: `timestamp`, `cpu_time` and `duration` (**microseconds →
÷1000 for ms**), `logical_reads`, `writes`, `row_count`, and the actions `server_principal_name`
(login), `client_app_name` (program), `client_hostname`, `database_name`. **Incremental:** keep a
watermark (last event timestamp seen, stored in `collection_run` or a small state row) and skip
events at/under it. **Aggregate** the window's events by `(login_name, program_name, host_name,
database_name)` → one `fact_workload` row each (sum cpu_ms/duration_ms/reads/writes/rows, count execs).
Upsert via MERGE on the natural key (`attribution_hash` handles key width). Cadence: every 15–30 min.
Workload *category* is NOT resolved here — `rpt.workload_by_category` joins to `dim_workload_class` so
the mapping stays editable without re-collecting.

**sessions.py** *(OPTIONAL, zero-DDL fallback if the XE session can't be deployed)* — purely
read-only sampler. Snapshot `sys.dm_exec_requests r JOIN sys.dm_exec_sessions s ON r.session_id =
s.session_id` where `s.is_user_process = 1 AND r.session_id <> @@SPID`; capture `login_name`,
`program_name`, `host_name`, `DB_NAME(r.database_id)`, and in-flight `cpu_time`/`logical_reads`.
Aggregate to one `fact_session_sample` row per `(login, program, host, db)` per sample
(active_requests count + summed in-flight cpu/reads). Approximate (samples miss sub-interval
queries) — use only when XE is unavailable. Cadence: every 1–5 min.

**concurrency.py** — *near-real-time concurrency timeline.* Runs the single-row concurrency snapshot
(same logic as `rt.concurrency_now` in `sql/realtime_queries.sql`: user_sessions, running, runnable,
suspended, blocked, memory_grants_pending, longest_open_tran_sec) against the monitored instance
(read-only) and writes **one `fact_concurrency` row per minute** to the repo. Short retention (7 days).
This powers the concurrency *timeline*; the instantaneous grid comes from the `rt.*` live views, not
this collector. Cadence: every 1 min.

### Real-time layer (`sql/realtime_queries.sql`) — no collector, no persistence

The `rt.*` views are live DMV queries for the real-time dashboard section (query performance +
concurrency): `rt.active_requests` (what's running, costliest first), `rt.blocking` (who blocks whom),
`rt.live_waits` (what's waited on right now), `rt.concurrency_now`, `rt.memory_grants`,
`rt.tempdb_by_session`, `rt.long_transactions`, `rt.cpu_schedulers`. They are read-only and need only
`VIEW SERVER STATE`. **Claude Code does not build these** — they're provided. They can be deployed as
one-time read-only views on the monitored instance (needs `CREATE VIEW` + a `rt` schema) **or** used as
Power BI DirectQuery native queries / SSMS ad-hoc with zero deployment.

## 12. `rpt.*` view specifications (`sql/rpt_views.sql`)

- **rpt.cpu_timeline** — select of `fact_cpu`.
- **rpt.cpu_hour_heatmap** —
  ```sql
  SELECT source_instance,
         DATENAME(weekday, sample_time_utc) AS day_of_week,
         DATEPART(hour,   sample_time_utc)  AS hour_of_day,
         AVG(CAST(sql_cpu_pct AS DECIMAL(5,2))) AS avg_sql_cpu_pct
  FROM dbo.fact_cpu
  GROUP BY source_instance, DATENAME(weekday, sample_time_utc), DATEPART(hour, sample_time_utc);
  ```
  (Grouped in UTC; convert to local in Power BI if needed.)
- **rpt.wait_deltas** — LAG with restart handling so deltas are never negative:
  ```sql
  SELECT source_instance, snapshot_time_utc, wait_type,
         CASE WHEN wait_ms_cum >= prev THEN wait_ms_cum - prev ELSE wait_ms_cum END AS wait_ms_delta
  FROM (
      SELECT *, LAG(wait_ms_cum) OVER (PARTITION BY source_instance, wait_type
                                       ORDER BY snapshot_time_utc) AS prev
      FROM dbo.fact_wait_stats
  ) z
  WHERE prev IS NOT NULL;
  -- add a wait_category CASE: CPU / IO / Lock / Memory / Parallelism / Network
  ```
- **rpt.top_queries** — from `fact_query_store`, ranked by avg CPU and by avg duration over a rolling
  window; expose rank columns so Power BI shows Top-N without heavy DAX.
- **rpt.table_storage_latest** / **rpt.table_growth** — latest snapshot per table; growth vs N days ago.
- **rpt.table_access_daily** — per-day access deltas from `fact_table_usage`, with restart handling
  (same LAG pattern as `rpt.wait_deltas`): `reads_day = seeks_day + scans_day + lookups_day`,
  `writes_day`, and keep `scans_day`/`seeks_day` separate so scan-heaviness is visible. This is the
  "most accessed tables per day" view — rank by `reads_day`.
  ```sql
  SELECT source_instance, snapshot_date, database_name, schema_name, table_name,
         CASE WHEN scans_cum  >= p_scans  THEN scans_cum  - p_scans  ELSE scans_cum  END AS scans_day,
         CASE WHEN seeks_cum  >= p_seeks  THEN seeks_cum  - p_seeks  ELSE seeks_cum  END AS seeks_day,
         CASE WHEN updates_cum>= p_upd    THEN updates_cum- p_upd    ELSE updates_cum END AS writes_day
  FROM (
      SELECT *,
        LAG(scans_cum)   OVER (PARTITION BY source_instance,database_name,schema_name,table_name ORDER BY snapshot_date) p_scans,
        LAG(seeks_cum)   OVER (PARTITION BY source_instance,database_name,schema_name,table_name ORDER BY snapshot_date) p_seeks,
        LAG(updates_cum) OVER (PARTITION BY source_instance,database_name,schema_name,table_name ORDER BY snapshot_date) p_upd
      FROM dbo.fact_table_usage
  ) z WHERE p_scans IS NOT NULL;   -- add lookups_day the same way; reads_day = seeks+scans+lookups
  ```
- **rpt.table_access_trend** — rolling N-day rollup of `rpt.table_access_daily` for the 3–4 month
  access-pattern analysis (avg reads/day, scan ratio = `scans / NULLIF(seeks,0)`).
- **rpt.index_opportunities** — latest `fact_index_ops`; missing ranked by `impact_score`, unused where
  `reads = 0 AND writes > 0`.
- **rpt.tuning_candidates** — *the analytical payoff.* Joins **size** (`rpt.table_storage_latest`)
  × **access** (`rpt.table_access_trend`) × **index opportunities** (`fact_index_ops`) per table to
  rank tuning targets: large `data_kb` + high `scan_ratio` + a high-impact missing index ⇒ "add index";
  large + unused indexes present ⇒ "drop index"; large + high writes ⇒ review fill factor /
  fragmentation. Expose the contributing columns so the dashboard can explain *why* each table is a
  candidate.
- **rpt.health_summary** — latest `fact_health` per DB with RAG flags from `config.yaml` thresholds.
- **rpt.workload_by_category** — `fact_workload` joined to `dim_workload_class` (first-match by
  priority), grouped by `window_start_utc` + `workload_category`: sum `total_cpu_ms`,
  `total_logical_reads`, `exec_count`. This is the "what kind of workload is hogging resources" view
  (ETL/SSIS vs BI/Tableau vs App vs Ad-hoc). Classify with:
  ```sql
  CROSS APPLY (SELECT TOP 1 d.workload_category
               FROM dbo.dim_workload_class d
               WHERE f.program_name LIKE d.program_like
               ORDER BY d.priority) c
  ```
- **rpt.top_logins** — `fact_workload` grouped by `login_name` (optionally + category), ranked by
  total CPU and by total reads over a rolling window. This is the "best/worst users" view.

## 13. Offline testing (no database)

`tests/conftest.py` provides `FakeCursor` (returns rows from `tests/fixtures/*.json`) and
`FakeConnection`. Each collector test:
- feeds a canned DMV rowset → asserts `transform()` output (e.g. `other_cpu_pct = 100 - sql - idle`,
  microseconds→ms division, restart clamp ≥ 0);
- asserts `upsert_sql()` references the correct columns/keys;
- asserts `--dry-run` performs **zero** writes.
`run.py --help` must work with no DB/driver present. Optional: a `sqlglot.parse` smoke test over
`sql/*.sql` (best-effort; some DMV/XML T-SQL may not fully parse — do not block on it).

## 14. Porting checklist (the human, inside the org — not Claude Code)

1. Bring the repo inside the boundary.
2. Install ODBC Driver 18 + `pip install -r requirements.txt`.
3. `sqlcmd -S <repo> -i sql/repo_schema.sql` (then `rpt_views.sql`, `retention.sql`,
   `workload_attribution.sql` PART A).
4. **One-time, in change control:** run `workload_attribution.sql` PART B on the **monitored**
   instance (needs `ALTER ANY EVENT SESSION`) to create + start the `Observability_Workload` XE
   session; point its target at a SQL-writable folder.
5. Fill `config.yaml`; grant the collector account `VIEW SERVER STATE` (+ DB state, `msdb` read).
6. `python run.py --task cpu --dry-run` against the real instance → then a real run; then
   `--task workload`.
7. Reconcile a few values against SSMS (CPU, backups, table size; spot-check a known login in
   `rpt.top_logins`).
8. Schedule all tasks + retention.
9. Install the Power BI gateway; connect Power BI to `rpt.*` (Import); publish + scheduled refresh.

*(Optional, richer query history: enable Query Store on target databases and set
`query_perf.source: query_store` — see §16. Not required; the default plan-cache source needs no DB
change.)*

*(Real-time dashboard: either deploy `sql/realtime_queries.sql` as read-only `rt.*` views on the
monitored instance (one-time `CREATE VIEW`), or use its SELECTs as Power BI DirectQuery native queries
with Automatic Page Refresh — no deployment needed.)*

## 15. Build plan — phased, offline acceptance criteria + a commit per task

**Phase 1 — scaffold, db layer, volatile collectors** (branch `feat/phase1`)
- [x] 1.1 Scaffold `src/`, `run.py`, `tests/` around the **provided** root files (`requirements.txt`,
  `requirements-dev.txt`, `.gitignore`, `config.yaml`, `.env.example`, `sql/repo_schema.sql`,
  `sql/workload_attribution.sql`). ✅ `python run.py --help` works; `git status` shows no secrets. **Commit.**
  — DONE (commit `26711c3`): `src/__init__.py`, `src/collectors/__init__.py`, `tests/__init__.py`,
  `run.py` (argparse CLI shell: `--task`/`--dry-run`/`--config`; task dispatch is a placeholder until
  1.2-1.4 wire in config/db/collectors), `tests/test_run_cli.py` (help exits 0, missing `--task` exits
  non-zero). `ruff check .` clean, `pytest -q` green (2 passed). Repo git-initialized on `feat/phase1`;
  no `.env`/secrets tracked. Remote/push intentionally NOT done (Section 9 — human's job).
- [x] 1.2 `src/config.py` + `src/db.py` (factory + run-logging, import-isolated). ✅ `ruff` clean; config
  load+validate unit-tested. **Commit.**
  — DONE: `src/config.py` (`load_config`/`validate_config`/`env_var_prefix`), `src/db.py`
  (`build_connection_string`, `connect` with pyodbc imported lazily inside the function so the
  module loads with no driver installed, `execute`, `start_run`/`finish_run` for `collection_run`
  logging). `tests/test_config.py` + `tests/test_db.py` (inline fake cursor/connection; formalized
  as shared fixtures in Task 1.3). Fixed a real YAML bug in the provided `config.yaml` line 35
  (`table_access:{` missing a space before `{`, broke PyYAML flow-mapping parse). `ruff check .`
  clean, `pytest -q` green (26 passed).
- [x] 1.3 `base.Collector` + `conftest.py` fakes/fixtures. ✅ a sample collector runs `--dry-run` against
  the fake with zero writes. **Commit.**
  — DONE: `src/collectors/base.py` (`Collector` ABC: `source_query()`/`transform()`/`columns()`/
  `upsert_sql()` contract; `run(source_conn, repo_conn, dry_run)` handles collection_run logging via
  `db.start_run`/`finish_run`, persists via `_persist()` looping `cursor.execute(upsert_sql, ...)` per
  row in `columns()` order, catches persist errors and marks the run `failed` before re-raising).
  `tests/conftest.py` — shared `FakeCursor`/`FakeConnection` (single cursor per connection; `rows`/
  `columns` back `fetchall()`+`description`, `scalar` backs `fetchone()` for the `OUTPUT INSERTED.run_id`
  pattern) + `load_fixture()` for future canned JSON DMV rowsets in `tests/fixtures/`. `test_db.py`
  refactored to reuse these fakes instead of its own inline copies. `tests/test_base_collector.py` adds
  a throwaway `SampleCollector` (not a real collector — those are Task 1.4) proving dry-run does zero
  writes, a real run persists+commits+logs success, and a persist error marks the run failed.
  `ruff check .` clean, `pytest -q` green (29 passed).
- [x] 1.4 `cpu`, `waits`, `query_perf` collectors (Section 11). ✅ transform unit tests pass (CPU math,
  µs→ms, query_hash aggregation); upsert SQL columns asserted. **Commit + push; merge to main.**
  — DONE: `src/collectors/cpu.py` (ring-buffer query verbatim from spec; `other_cpu_pct = 100 - sql -
  idle` clamped to >= 0), `src/collectors/waits.py` (raw cumulative + one `snapshot_time_utc` per run;
  `wait_type_exclusions` filtered in Python from config), `src/collectors/query_perf.py` (plan-cache
  query_hash aggregation; single SQL statement UNIONs a top-N-by-CPU pass with a top-N-by-logical-reads
  pass, `top_n` from `config.yaml` embedded as a validated int; µs->ms division done in SQL; Python-side
  dedup by `query_hash` as a defensive safety net; NULL `query_sql_text` handled for evicted plans).
  Each collector has fixture-driven tests in `tests/test_{cpu,waits,query_perf}.py` (JSON fixtures in
  `tests/fixtures/`) plus a `columns()`/`upsert_sql()` placeholder-count assertion so a future column
  add can't silently desync the two. `run.py` now has a real `TASK_REGISTRY` dispatching these three
  (env-var credential lookup, connect source+repo, run collector, always close connections even on
  failure) — the remaining `TASK_NAMES` print "not yet implemented" until Phase 2. Manually verified
  `python run.py --task cpu --dry-run` fails gracefully (`No module named 'pyodbc'`, exit 1, no
  traceback) since this build machine has no ODBC driver — expected per Section 1.
  `ruff check .` clean, `pytest -q` green (46 passed).
  **Not yet done: push + merge to main** — holding per user instruction to push later; still on local
  branch `feat/phase1` with no remote configured.

  **End of Phase 1.** All four tasks (1.1-1.4) complete on `feat/phase1`. Next: Phase 2 (`feat/phase2`)
  — 2.1 storage/index_ops/table_access/health collectors.

**Phase 2 — daily collectors, attribution, views, retention** (branch `feat/phase2`)
- [x] 2.1 `storage`, `index_ops`, `table_access`, `health` collectors. ✅ transform tests pass (incl.
  table-grain rollup of usage stats). **Commit.**
  — DONE: extended `src/collectors/base.py` with a `fetch_rows()` seam (default: run
  `source_query()` once) plus a new `PerDatabaseCollector` subclass that resolves target
  databases from `config.yaml monitored_instances[].databases` (or discovers all online
  user databases via `sys.databases WHERE database_id > 4` when that list is empty), then
  runs `USE [db]; <source_query()>` once per database, tagging each row with
  `database_name` before `transform()`. `storage.py`/`index_ops.py`/`table_access.py`
  subclass `PerDatabaseCollector` (their DMVs are current-database-scoped);
  `health.py` stays a plain `Collector` since `sys.databases`+`msdb` are server-wide and
  already return one row per DB. Specifics: storage computes `index_kb`/`unused_kb`
  directly in SQL from `dm_db_partition_stats` page counts; index_ops UNION ALLs a
  missing-index pull (`impact_score`+`detail`) with an unused-index pull
  (`reads`/`writes`, candidate when zero reads + positive writes); table_access rolls all
  indexes of a table to table grain via `GROUP BY schema,table` with a `CROSS APPLY VALUES`
  trick for `MAX` across seek/scan/lookup timestamps; health reads `msdb.dbo.backupset` +
  `sysjobhistory`/`sysjobs` (`agent_datetime` for the 24h window) via `OUTER APPLY`.
  `run.py TASK_REGISTRY` now dispatches all seven implemented tasks. Tests:
  `tests/test_per_database_collector.py` (generic looping/discovery/escaping behavior with
  a throwaway `SamplePerDbCollector`) plus one fixture-driven test file per real collector
  (`test_storage.py`, `test_index_ops.py`, `test_table_access.py`, `test_health.py`).
  `ruff check .` clean, `pytest -q` green (63 passed).
- [x] 2.2 `workload.py` (XE reader) + `sessions.py` (optional sampler) + `concurrency.py` (1-min
  snapshot → `fact_concurrency`). ✅ XE-event parsing unit-tested against canned `.xel`-shaped XML
  fixtures (µs→ms, watermark filter, aggregation keys); concurrency transform tested; asserts no DDL.
  **Commit.**
  — DONE: `workload.py` parses raw `event_data` XML from `sys.fn_xe_file_target_read_file` via
  `xml.etree.ElementTree` (`parse_xe_event()`), never creates/alters the XE session. Watermark =
  `MAX(started_at_utc)` from `dbo.collection_run` for this `source_instance`/task/`success` (read via
  a `run()` override that fetches it from `repo_conn` before calling `super().run()`); events at/under
  it are skipped. Aggregates by `(login_name, program_name, host_name, database_name)`; `upsert_sql()`
  matches `fact_workload`'s computed `attribution_hash` column by recomputing the identical
  `HASHBYTES('SHA2_256', CONCAT(...))` expression in the MERGE's `ON` clause (can't insert into a
  computed column directly). `sessions.py` — zero-DDL fallback, aggregates `dm_exec_requests` by the
  same 4-column key (no µs conversion needed, `cpu_time` is already ms there). `concurrency.py` reuses
  `rt.concurrency_now`'s exact logic (aliased to `fact_concurrency`'s column names). `run.py`'s
  `TASK_REGISTRY` now covers all 10 collectors from the build plan, so the old "not yet implemented"
  placeholder branch and the separate `TASK_NAMES` list were dead code — removed; `--task` choices are
  now derived directly from `TASK_REGISTRY`. `ruff check .` clean, `pytest -q` green (78 passed).

  **Known issue flagged for the porting checklist (Section 14):** the provided
  `sql/workload_attribution.sql` declares `fact_session_sample`'s key columns (`login_name`,
  `program_name`, `host_name`, `database_name`) as nullable (`NULL`) but also includes them in its
  `PRIMARY KEY` constraint — SQL Server rejects `PRIMARY KEY` on nullable columns at deploy time
  ("Cannot define PRIMARY KEY constraint on nullable column"). Not fixed here since it's a provided
  file outside this task's scope and doesn't block Python collector work (sessions.py's column names/
  shapes are unaffected) — flagging so it's fixed before running `workload_attribution.sql` PART A at
  port time.
- [x] 2.3 `sql/rpt_views.sql` incl. `rpt.workload_by_category` + `rpt.top_logins` (Section 12). ✅ optional
  sqlglot parse; manual review vs spec. **Commit.**
  — DONE: all 12 views from Section 12 built (`cpu_timeline`, `cpu_hour_heatmap`, `wait_deltas` +
  `wait_category`, `top_queries`, `table_storage_latest`, `table_growth` (vs. closest snapshot >= 30
  days prior), `table_access_daily` (LAG-with-floor restart handling, extended with `lookups_day` per
  the spec note), `table_access_trend` (rolling 90-day), `index_opportunities` (latest snapshot per
  instance/db + `impact_rank` for missing-index rows), `tuning_candidates` (size x access x index
  opportunities join), `health_summary` (RAG via `state_desc`/`job_failures_24h`/backup-age thresholds
  mirroring `config.yaml`), `workload_by_category`, `top_logins`). Two deviations from the Section 12
  text, both intentional and noted inline in the SQL:
  1. `rpt.top_queries` reads `dbo.fact_query_perf` — Section 12 names `fact_query_store`, which
     doesn't exist anywhere in `repo_schema.sql` or the `query_perf` collector; a spec typo.
  2. `rpt.workload_by_category` uses `OUTER APPLY` instead of the sketched `CROSS APPLY`, so
     `fact_workload` rows with a NULL `program_name` still surface (tagged `Unknown`) instead of
     silently vanishing, since `NULL LIKE '%'` is never `TRUE`.
  Also tightened `tuning_candidates`' size<->index-opportunities join from a bare `LIKE '...%'` (which
  would false-match e.g. `dbo.Order` against table `Orders`) to an exact match or a `.`-bounded prefix.
  `tests/test_sql_parse.py` added: sqlglot (`dialect="tsql"`) parses every GO-separated batch across
  all four `sql/*.sql` files — 0 failures. `sql/rpt_views.sql.TODO` removed. `ruff check .` clean,
  `pytest -q` green (82 passed).
- [x] 2.4 `sql/retention.sql` (batched deletes per `retention_days`, incl. `fact_workload`/
  `fact_session_sample`). ✅ parse/review. **Commit + push; merge.**
  — DONE: one `DELETE TOP (@BatchSize) ... WHILE @@ROWCOUNT > 0` loop per fact table (+
  `collection_run`), batch size 5000, retention windows matching `config.yaml`
  `retention_days:` exactly (cpu/waits 30d, query_perf/workload 120d, session_sample 14d,
  concurrency 7d, table_storage/table_usage/index_ops/health 365d, collection_run 90d).
  `tests/test_sql_parse.py` now covers 5 `sql/*.sql` files, all passing. `sql/retention.sql.TODO`
  removed. `ruff check .` clean, `pytest -q` green (83 passed).

  **End of Phase 2.** All of 2.1-2.4 complete on `feat/phase2`. Every collector, every rpt.* view,
  and retention are built and offline-tested. Not yet done: push + merge to main (holding per
  instruction, same as Phase 1). Next: Phase 3 (`feat/phase3`) — docs & CI.

**Phase 3 — docs & CI** (branch `feat/phase3`)
- [x] 3.1 Verify/extend the provided `README.md`; add `.github/workflows/ci.yml` (ruff + pytest via
  `requirements-dev.txt`, no DB). ✅ CI green on push.
  — DONE: README.md was missing the `concurrency` collector entirely (added in Task 2.2, after the
  README was written) — added it to the "What it collects" table, the `--task` list, and a `* * * * *`
  cron line. `.github/workflows/ci.yml` added: checkout, Python 3.11, `pip install -r
  requirements-dev.txt` (no pyodbc), `ruff check .`, `pytest -q`, `python run.py --help` — runs on
  push/PR to any branch. Not yet verified green on GitHub Actions since there's no remote configured
  yet (Section 9 — human sets up the remote); will confirm once pushed.
- [x] 3.2 Final pass: full `pytest` green, `ruff` clean, no DB references in tests. **Commit + push; merge.**
  — DONE: confirmed `pyodbc` is imported exactly once in the whole `src/` tree, lazily inside
  `db.connect()` (grepped); the only "pyodbc" mentions in `tests/` are a docstring/comment describing
  the fakes and a positive assertion that `"pyodbc" not in sys.modules`. `run.py --help` exits 0 with
  no ODBC driver installed. `ruff check .` clean, `pytest -q` green (83 passed, unchanged by the docs
  changes above).

  **Definition of done (Section 17) met:** all 10 collectors + `rpt.*` views + retention written to
  spec, tests green against the mocked DB layer, ruff clean, `run.py --help` works driver-less, nothing
  in the repo attempts a live connection, every phase committed with conventional messages. Live
  validation remains the human's Phase-14 porting step. **Not done: push to remote / merge phases to
  main** — holding throughout per instruction ("you can push it later"); repo has no remote configured.

## 16. What this platform measures and why (the metrics that matter)

Each metric exists to answer a specific management/operations question:

| Metric (source) | Question it answers | Original goal it serves |
|---|---|---|
| **CPU over time + hour×day heatmap** (`fact_cpu`) | When are we hot? Where's headroom? | "CPU best/worst times" |
| **Wait statistics, categorized deltas** (`fact_wait_stats`) | *Why* is it slow (CPU vs IO vs locking vs memory)? | Platform observability — root cause |
| **Top queries** (`fact_query_perf`) | What's most expensive to run? | "Best/worst performing queries" |
| **Workload by type** (`fact_workload` → `dim_workload_class`) | Is ETL/SSIS, BI/Tableau, app code, or ad-hoc extracts hogging resources? | "What kind of workload is hogging resources" |
| **Top logins/users** (`fact_workload`) | Which users/logins cost the most CPU and IO? | "Best/worst users" |
| **Table volume + growth** (`fact_table_storage`) | What's biggest and growing fastest? | "Most voluminous tables" |
| **Table access per day + patterns** (`fact_table_usage`) | Which tables are hit most? Scan-heavy or seek-heavy? | "Most accessed tables per day" |
| **Index opportunities** (`fact_index_ops`) | What concrete tuning actions exist? | "Add useful / drop unused indexes" |
| **Tuning candidates** (size × access × index opps) | Which tables are the highest-value tuning targets, and why? | Access-pattern + tuning analysis |
| **Backups, recovery, job failures** (`fact_health`) | Are we safe and running? | Platform observability — reliability |
| **Live active requests / query perf** (`rt.active_requests`) | What is running right now, and what's costly? | Real-time query performance |
| **Live blocking + waits** (`rt.blocking`, `rt.live_waits`) | Who's blocked right now, waiting on what? | Real-time concurrency |
| **Concurrency timeline** (`fact_concurrency`) | How have active/blocked/runnable sessions trended over the last hours? | Real-time concurrency (trend) |

**History horizon for access-pattern analysis:** the 3–4 month "dig" runs on `fact_table_usage` +
`fact_query_perf`. `fact_table_usage` accumulates cleanly (retain 365 days). Query history is the weak
spot on **2019 without Query Store**: the plan cache is volatile (evicts on memory pressure, recompiles,
restart), so `fact_query_perf` is a periodic *snapshot* of top consumers — it captures the heavy hitters
well but misses queries that never coincide with a snapshot. If robust multi-month query history
matters, **enable Query Store** on the target databases (fully supported on 2019 — a one-time
`ALTER DATABASE <db> SET QUERY_STORE = ON (...)` in change control), set `query_perf.source:
query_store`, and size its retention/max storage to your window. Either way the repo accumulates
*forward* from when collection starts, so start collecting early.

**Future (deferred, per your note):** the repo is a clean star-style schema, so it ports to Snowflake
easily — Cortex Analyst / Cortex Search could later sit on top for natural-language questions over the
access history. Not built here; noted only so the schema stays portable.

**Attribution mechanism note:** login + workload-type attribution uses **Extended Events** (and an
optional DMV sampler) — **no extra license; built into every SQL Server edition.** The only "cost" is
a one-time XE session deployment (PART B) requiring `ALTER ANY EVENT SESSION`. The runtime collector
stays read-only. Generic `.Net SqlClient` traffic is mapped to "App (generic)" and flagged to refine
by setting `Application Name=` in those connection strings.

**Deliberately deferred (clean extension points, not built in Phase 1):**
- *Blocking & deadlocks* — high value operationally, lower value for a management trend view.
- *IO file latency* (`sys.dm_io_virtual_file_stats`) — the top candidate to add next for "is storage
  the bottleneck."

## 17. Definition of done (offline)

All collectors (`cpu`, `waits`, `query_perf`, `storage`, `index_ops`, `table_access`, `health`,
`workload`; `sessions` optional) and the `rpt.*`/retention SQL are written to spec; `pytest` is green
against the mocked DB layer; `ruff` is clean; `run.py --help` works with no driver present; nothing in
the repo attempts a live connection; every phase is committed with conventional messages and pushed.
Live validation is explicitly the human's Phase-14 porting step.
