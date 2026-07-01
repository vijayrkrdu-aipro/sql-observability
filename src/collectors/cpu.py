"""Per-minute CPU history from the scheduler ring buffer (Section 11).

Runs against the monitored instance (master context). Returns ~256 minutes of
per-minute history per poll; upsert by sample_time_utc (idempotent).
"""
from __future__ import annotations

from typing import Any

from src.collectors.base import Collector

_SOURCE_QUERY = """
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
"""

_COLUMNS = ("source_instance", "sample_time_utc", "sql_cpu_pct", "other_cpu_pct", "idle_pct")

_UPSERT_SQL = """
MERGE dbo.fact_cpu AS tgt
USING (SELECT ? AS source_instance, ? AS sample_time_utc, ? AS sql_cpu_pct, ? AS other_cpu_pct, ? AS idle_pct) AS src
ON tgt.source_instance = src.source_instance AND tgt.sample_time_utc = src.sample_time_utc
WHEN MATCHED THEN UPDATE SET
    sql_cpu_pct = src.sql_cpu_pct, other_cpu_pct = src.other_cpu_pct, idle_pct = src.idle_pct
WHEN NOT MATCHED THEN
    INSERT (source_instance, sample_time_utc, sql_cpu_pct, other_cpu_pct, idle_pct)
    VALUES (src.source_instance, src.sample_time_utc, src.sql_cpu_pct, src.other_cpu_pct, src.idle_pct);
"""


class CpuCollector(Collector):
    task_name = "cpu"

    def source_query(self) -> str:
        return _SOURCE_QUERY

    def transform(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for r in rows:
            sql_pct = r["sql_cpu_pct"]
            idle_pct = r["idle_pct"]
            other_pct = max(0, 100 - sql_pct - idle_pct)
            out.append(
                {
                    "source_instance": self.source_instance,
                    "sample_time_utc": r["sample_time_utc"],
                    "sql_cpu_pct": sql_pct,
                    "other_cpu_pct": other_pct,
                    "idle_pct": idle_pct,
                }
            )
        return out

    def upsert_sql(self) -> str:
        return _UPSERT_SQL

    def columns(self) -> tuple[str, ...]:
        return _COLUMNS
