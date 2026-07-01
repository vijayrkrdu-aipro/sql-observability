"""Near-real-time concurrency timeline -- one fact_concurrency row per minute (Section 11).

Same logic as rt.concurrency_now in sql/realtime_queries.sql, aliased to fact_concurrency's
column names (running/runnable/suspended vs. that view's runnable_cpu_pressure/
suspended_waiting). This collector is the one place that bridges the real-time and historic
layers: rt.* views show the instantaneous grid, this gives a persisted timeline.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from src.collectors.base import Collector

_SOURCE_QUERY = """
SELECT
    (SELECT COUNT(*) FROM sys.dm_exec_sessions WHERE is_user_process = 1)              AS user_sessions,
    (SELECT COUNT(*) FROM sys.dm_exec_requests WHERE status = 'running')               AS running,
    (SELECT COUNT(*) FROM sys.dm_exec_requests WHERE status = 'runnable')              AS runnable,
    (SELECT COUNT(*) FROM sys.dm_exec_requests WHERE status = 'suspended')             AS suspended,
    (SELECT COUNT(*) FROM sys.dm_exec_requests WHERE blocking_session_id <> 0)         AS blocked,
    (SELECT COUNT(*) FROM sys.dm_exec_query_memory_grants WHERE grant_time IS NULL)    AS memory_grants_pending,
    (SELECT ISNULL(MAX(DATEDIFF(SECOND, at.transaction_begin_time, SYSDATETIME())), 0)
       FROM sys.dm_tran_active_transactions at)                                        AS longest_open_tran_sec;
"""

_COLUMNS = (
    "source_instance",
    "sample_time_utc",
    "user_sessions",
    "running",
    "runnable",
    "suspended",
    "blocked",
    "memory_grants_pending",
    "longest_open_tran_sec",
)

_UPSERT_SQL = """
MERGE dbo.fact_concurrency AS tgt
USING (SELECT ? AS source_instance, ? AS sample_time_utc, ? AS user_sessions, ? AS running,
              ? AS runnable, ? AS suspended, ? AS blocked, ? AS memory_grants_pending,
              ? AS longest_open_tran_sec) AS src
ON tgt.source_instance = src.source_instance AND tgt.sample_time_utc = src.sample_time_utc
WHEN MATCHED THEN UPDATE SET
    user_sessions = src.user_sessions, running = src.running, runnable = src.runnable,
    suspended = src.suspended, blocked = src.blocked,
    memory_grants_pending = src.memory_grants_pending, longest_open_tran_sec = src.longest_open_tran_sec
WHEN NOT MATCHED THEN
    INSERT (source_instance, sample_time_utc, user_sessions, running, runnable, suspended,
            blocked, memory_grants_pending, longest_open_tran_sec)
    VALUES (src.source_instance, src.sample_time_utc, src.user_sessions, src.running, src.runnable,
            src.suspended, src.blocked, src.memory_grants_pending, src.longest_open_tran_sec);
"""


class ConcurrencyCollector(Collector):
    task_name = "concurrency"

    def source_query(self) -> str:
        return _SOURCE_QUERY

    def transform(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sample_time_utc = self._utcnow()
        return [
            {
                "source_instance": self.source_instance,
                "sample_time_utc": sample_time_utc,
                "user_sessions": r["user_sessions"],
                "running": r["running"],
                "runnable": r["runnable"],
                "suspended": r["suspended"],
                "blocked": r["blocked"],
                "memory_grants_pending": r["memory_grants_pending"],
                "longest_open_tran_sec": r["longest_open_tran_sec"],
            }
            for r in rows
        ]

    def upsert_sql(self) -> str:
        return _UPSERT_SQL

    def columns(self) -> tuple[str, ...]:
        return _COLUMNS

    @staticmethod
    def _utcnow() -> dt.datetime:
        return dt.datetime.now(dt.timezone.utc)
