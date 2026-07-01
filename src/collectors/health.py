"""Backups, recovery model, DB state, job failures -- one row per DB per day (Section 11).

Unlike storage/index_ops/table_access, this reads sys.databases + msdb directly (both
server-wide, not current-database scoped), so it's a plain Collector -- one query already
returns one row per database, no USE-per-database looping needed.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from src.collectors.base import Collector

_SOURCE_QUERY = """
SELECT
    d.name AS database_name,
    d.recovery_model_desc AS recovery_model,
    d.state_desc AS state_desc,
    bk.last_full_backup_utc,
    bk.last_log_backup_utc,
    ISNULL(jf.job_failures_24h, 0) AS job_failures_24h
FROM sys.databases d
OUTER APPLY (
    SELECT
        MAX(CASE WHEN b.type = 'D' THEN b.backup_finish_date END) AS last_full_backup_utc,
        MAX(CASE WHEN b.type = 'L' THEN b.backup_finish_date END) AS last_log_backup_utc
    FROM msdb.dbo.backupset b
    WHERE b.database_name = d.name
) bk
OUTER APPLY (
    SELECT COUNT(*) AS job_failures_24h
    FROM msdb.dbo.sysjobhistory h
    JOIN msdb.dbo.sysjobs j ON j.job_id = h.job_id
    WHERE h.run_status = 0
      AND msdb.dbo.agent_datetime(h.run_date, h.run_time) >= DATEADD(HOUR, -24, GETDATE())
) jf
WHERE d.database_id > 4;
"""

_COLUMNS = (
    "source_instance",
    "snapshot_date",
    "database_name",
    "last_full_backup_utc",
    "last_log_backup_utc",
    "recovery_model",
    "state_desc",
    "job_failures_24h",
)

_UPSERT_SQL = """
MERGE dbo.fact_health AS tgt
USING (SELECT ? AS source_instance, ? AS snapshot_date, ? AS database_name, ? AS last_full_backup_utc,
              ? AS last_log_backup_utc, ? AS recovery_model, ? AS state_desc, ? AS job_failures_24h) AS src
ON tgt.source_instance = src.source_instance AND tgt.snapshot_date = src.snapshot_date
   AND tgt.database_name = src.database_name
WHEN MATCHED THEN UPDATE SET
    last_full_backup_utc = src.last_full_backup_utc, last_log_backup_utc = src.last_log_backup_utc,
    recovery_model = src.recovery_model, state_desc = src.state_desc, job_failures_24h = src.job_failures_24h
WHEN NOT MATCHED THEN
    INSERT (source_instance, snapshot_date, database_name, last_full_backup_utc, last_log_backup_utc,
            recovery_model, state_desc, job_failures_24h)
    VALUES (src.source_instance, src.snapshot_date, src.database_name, src.last_full_backup_utc,
            src.last_log_backup_utc, src.recovery_model, src.state_desc, src.job_failures_24h);
"""


class HealthCollector(Collector):
    task_name = "health"

    def source_query(self) -> str:
        return _SOURCE_QUERY

    def transform(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        snapshot_date = self._today()
        return [
            {
                "source_instance": self.source_instance,
                "snapshot_date": snapshot_date,
                "database_name": r["database_name"],
                "last_full_backup_utc": r["last_full_backup_utc"],
                "last_log_backup_utc": r["last_log_backup_utc"],
                "recovery_model": r["recovery_model"],
                "state_desc": r["state_desc"],
                "job_failures_24h": r["job_failures_24h"],
            }
            for r in rows
        ]

    def upsert_sql(self) -> str:
        return _UPSERT_SQL

    def columns(self) -> tuple[str, ...]:
        return _COLUMNS

    @staticmethod
    def _today() -> dt.date:
        return dt.datetime.now(dt.timezone.utc).date()
