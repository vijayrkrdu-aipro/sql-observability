# SQL Server Fleet Observability

A lightweight, Python-based observability platform for **SQL Server 2019**. Read-only collectors pull
performance, access, and health metrics from a monitored instance, store them on a separate repository
instance, and expose a stable `rpt.*` view layer that **Power BI** renders as a management dashboard.

> Build instructions for Claude Code live in `CLAUDE.md`. This README is the **operator runbook** for
> running the platform on your enterprise after the code is built and downloaded.

---

## What it collects

| Collector | Captures | Source | Cadence |
|---|---|---|---|
| `cpu` | Per-minute CPU (SQL vs other vs idle) | ring buffer | 15 min |
| `waits` | Cumulative wait stats (deltas in views) | `dm_os_wait_stats` | 15 min |
| `query_perf` | Top queries by CPU / reads / duration | **plan cache** (`dm_exec_query_stats`) | 60 min |
| `workload` | Login + workload-type attribution (ETL/BI/app/ad-hoc) | **Extended Events** | 30 min |
| `sessions` | *(optional)* live active-request sampler | DMVs | 5 min |
| `concurrency` | Near-real-time concurrency timeline (active/blocked/runnable) | DMVs | 1 min |
| `storage` | Table row counts and size | `dm_db_partition_stats` | daily |
| `index_ops` | Missing + unused index opportunities | index DMVs | daily |
| `table_access` | Per-day table access counts + patterns | `dm_db_index_usage_stats` | daily |
| `health` | Backups, recovery model, DB state, job failures | `msdb` / `sys.databases` | daily |
| `io_latency` | Per-file IO stats — is storage the bottleneck | `dm_io_virtual_file_stats` | 15 min |
| `blocking` | Point-in-time blocking chains (zero-DDL) | DMVs | 1 min |
| `deadlocks` | Deadlock events (zero-DDL, built-in session) | `system_health` XE | 15 min |

Power BI reads only the `rpt.*` views — never the raw tables — so internal schema changes never break
the dashboard. Query Store is **not required**; if you later enable it, set `query_perf.source:
query_store` in `config.yaml` for richer history.

## Architecture

```
Monitored SQL ──read-only──► Python collectors ──► Repository SQL (DBA_Observability)
                                                         │
                                                    rpt.* views ──► Gateway ──► Power BI
```
Collectors are scheduler-agnostic CLI jobs. Any scheduler (Control-M, Autosys, Task Scheduler, cron)
invokes them on a cadence.

---

## Prerequisites

- **Python 3.11+** and **ODBC Driver 18 for SQL Server** on the collector host.
- A **repository SQL Server instance** for `DBA_Observability` (not the monitored instance).
- Collector identity (ideally a Windows service account for Integrated auth) with, on the **monitored**
  instance: `VIEW SERVER STATE`, `VIEW DATABASE STATE` + `CONNECT` on monitored DBs, and read in
  `msdb`; and write access to `DBA_Observability` on the **repository** instance.
- **For the `workload` collector:** a one-time Extended Events session created by a DBA (needs
  `ALTER ANY EVENT SESSION`) — see `sql/workload_attribution.sql` PART B. The collector only *reads* it.
- **`io_latency`, `blocking`, and `deadlocks` need zero extra deployment** — they read
  `sys.dm_io_virtual_file_stats`, `sys.dm_exec_requests`, and the built-in `system_health` XE session
  (which every SQL Server instance runs by default), all with just `VIEW SERVER STATE`.

---

## Setup

```bash
# 1. Virtual environment + runtime deps (enterprise host)
python -m venv .venv
. .venv/Scripts/activate            # Windows  (Linux/macOS: . .venv/bin/activate)
pip install -r requirements.txt

# 2. Configure (no secrets in here)
#    Edit config.yaml: repository.server, monitored_instances[].name, cadences, thresholds,
#    workload.xe_file_glob, query_perf.source (plan_cache | query_store).
cp .env.example .env                # only if a connection uses SQL auth

# 3. Deploy repository objects
sqlcmd -S <repo_instance> -i sql/repo_schema.sql
sqlcmd -S <repo_instance> -i sql/workload_attribution.sql      # PART A (tables + seed)
sqlcmd -S <repo_instance> -i sql/rpt_views.sql
sqlcmd -S <repo_instance> -i sql/retention.sql

# 4. One-time on the MONITORED instance, in change control (needs ALTER ANY EVENT SESSION):
#    run PART B of workload_attribution.sql to create + start the Observability_Workload XE session,
#    pointing its target at a SQL-writable folder that matches workload.xe_file_glob.
```

---

## Running collectors

Always dry-run first — it collects and prints rowcounts but writes nothing:

```bash
python run.py --task cpu --dry-run
python run.py --task cpu
```

Tasks: `cpu`, `waits`, `query_perf`, `workload`, `sessions` (optional), `concurrency`, `storage`,
`index_ops`, `table_access`, `health`, `io_latency`, `blocking`, `deadlocks`. Exit `0` = success,
non-zero = failure (and a `failed` row is written to `collection_run`).

Check what ran:

```sql
SELECT TOP 50 task, source_instance, status, row_count, started_at_utc, error_message
FROM   DBA_Observability.dbo.collection_run
ORDER BY started_at_utc DESC;
```

---

## Scheduling

Keep cadence in the scheduler; `cadence_minutes` in `config.yaml` documents the intended frequency.

**cron example:**
```
*/15 * * * *  cd /opt/sql-observability && .venv/bin/python run.py --task cpu
*/15 * * * *  cd /opt/sql-observability && .venv/bin/python run.py --task waits
*/30 * * * *  cd /opt/sql-observability && .venv/bin/python run.py --task workload
0    * * * *  cd /opt/sql-observability && .venv/bin/python run.py --task query_perf
*    * * * *  cd /opt/sql-observability && .venv/bin/python run.py --task concurrency
*    * * * *  cd /opt/sql-observability && .venv/bin/python run.py --task blocking
*/15 * * * *  cd /opt/sql-observability && .venv/bin/python run.py --task io_latency
*/15 * * * *  cd /opt/sql-observability && .venv/bin/python run.py --task deadlocks
30   2 * * *  cd /opt/sql-observability && .venv/bin/python run.py --task storage
35   2 * * *  cd /opt/sql-observability && .venv/bin/python run.py --task index_ops
40   2 * * *  cd /opt/sql-observability && .venv/bin/python run.py --task table_access
45   2 * * *  cd /opt/sql-observability && .venv/bin/python run.py --task health
```
Run the daily `table_access` at a **consistent time** so per-day deltas align. **Windows Task
Scheduler / Control-M / Autosys:** one job per task calling the same commands. Each task is independent
and idempotent, so a single failure neither duplicates data nor blocks the others.

---

## Adding a second instance (config only)

1. Add an entry under `monitored_instances` in `config.yaml` (its `name` = `@@SERVERNAME`).
2. Grant the collector identity the permissions above (and deploy the XE session if you want workload
   attribution there).
3. Point your scheduler at the same commands — collectors loop over configured instances.

No code or schema change. Every fact row is stamped with `source_instance`.

---

## Query history note (SQL 2019)

`query_perf` reads the **plan cache**, which is volatile (evicts on memory pressure, recompiles,
restart). It reliably captures the heavy hitters but is a periodic *snapshot*, not complete history.
For robust multi-month query-history analysis, enable Query Store on the target databases
(`ALTER DATABASE <db> SET QUERY_STORE = ON (...)`) and set `query_perf.source: query_store`. Table
access history (`table_access`) accumulates cleanly regardless.

---

## Power BI

1. Install/configure the **on-prem data gateway** with line of sight to the repository.
2. Connect Power BI to `DBA_Observability`, **Import mode**, selecting only `rpt.*` views.
3. Publish; set scheduled refresh (every 15–30 min is plenty for management trends).

---

## Retention

`sql/retention.sql` purges old rows in batches per `retention_days` in `config.yaml`. Run it daily
(its own task or a repository SQL Agent job). Defaults: volatile facts 30 days, query/workload history
120 days, daily snapshots 365 days, `collection_run` 90 days.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `Data source name not found` / driver error | ODBC Driver 18 not installed on the collector host |
| Login / SSL errors | Check `encrypt` / `trust_server_certificate` in `config.yaml`; verify the account |
| `cpu` returns few/no rows | Confirm `VIEW SERVER STATE`; the ring buffer holds ~256 min — poll at least that often |
| `workload` empty | XE session not created/started, or `workload.xe_file_glob` doesn't match the .xel path |
| `query_perf` sparse | Plan cache was recently cleared/restarted — expected; consider Query Store |
| `deadlocks` misses some events | `system_health`'s ring_buffer target is capped and rolls over on a busy server — expected best-effort behavior, poll at least every 15 min |
| Negative deltas | Should never happen — the delta views handle restarts; check the view's reset logic |
| Duplicate rows | A collector isn't upserting on its natural key — re-check `persist()` |

---

## Recovering the monitoring stack

The platform is stateless code plus the repository database. To rebuild: recreate `DBA_Observability`
(re-run the `sql/*.sql` files), redeploy the repo + `config.yaml`, recreate the venv, re-enable the
scheduler jobs, and (if needed) recreate the XE session. **Back up `DBA_Observability`** — it is now a
production observability asset.

## Security notes

- Collectors are **read-only** on monitored instances — only `SELECT`. No writes there, ever.
- Credentials are never stored in code or `config.yaml`. Integrated auth stores no password at all.
- `.env` is gitignored; connection strings and credentials are never logged.
