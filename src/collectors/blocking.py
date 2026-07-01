"""Point-in-time blocking-chain snapshot (Phase 4, zero-DDL). One row per currently
blocked session per sample -- unlike concurrency.py's single aggregate row, each blocked
session is independently useful (who's blocked, by whom, on what). Short retention, same
spirit as fact_concurrency/fact_session_sample.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from src.collectors.base import Collector

_SOURCE_QUERY = """
SELECT
    r.session_id,
    r.blocking_session_id,
    r.wait_type,
    r.wait_time AS wait_time_ms,
    r.wait_resource,
    s.login_name,
    s.program_name,
    DB_NAME(r.database_id) AS database_name
FROM sys.dm_exec_requests r
JOIN sys.dm_exec_sessions s ON s.session_id = r.session_id
WHERE r.blocking_session_id <> 0;
"""

_COLUMNS = (
    "source_instance",
    "sample_time_utc",
    "session_id",
    "blocking_session_id",
    "wait_type",
    "wait_time_ms",
    "wait_resource",
    "login_name",
    "program_name",
    "database_name",
)

_UPSERT_SQL = """
MERGE dbo.fact_blocking_snapshot AS tgt
USING (SELECT ? AS source_instance, ? AS sample_time_utc, ? AS session_id, ? AS blocking_session_id,
              ? AS wait_type, ? AS wait_time_ms, ? AS wait_resource, ? AS login_name,
              ? AS program_name, ? AS database_name) AS src
ON tgt.source_instance = src.source_instance AND tgt.sample_time_utc = src.sample_time_utc
   AND tgt.session_id = src.session_id
WHEN MATCHED THEN UPDATE SET
    blocking_session_id = src.blocking_session_id, wait_type = src.wait_type,
    wait_time_ms = src.wait_time_ms, wait_resource = src.wait_resource, login_name = src.login_name,
    program_name = src.program_name, database_name = src.database_name
WHEN NOT MATCHED THEN
    INSERT (source_instance, sample_time_utc, session_id, blocking_session_id, wait_type,
            wait_time_ms, wait_resource, login_name, program_name, database_name)
    VALUES (src.source_instance, src.sample_time_utc, src.session_id, src.blocking_session_id,
            src.wait_type, src.wait_time_ms, src.wait_resource, src.login_name, src.program_name,
            src.database_name);
"""


class BlockingCollector(Collector):
    task_name = "blocking"

    def source_query(self) -> str:
        return _SOURCE_QUERY

    def transform(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sample_time_utc = self._utcnow()
        return [
            {
                "source_instance": self.source_instance,
                "sample_time_utc": sample_time_utc,
                "session_id": r["session_id"],
                "blocking_session_id": r["blocking_session_id"],
                "wait_type": r["wait_type"],
                "wait_time_ms": r["wait_time_ms"],
                "wait_resource": r["wait_resource"],
                "login_name": r["login_name"],
                "program_name": r["program_name"],
                "database_name": r["database_name"],
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
