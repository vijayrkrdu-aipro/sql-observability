"""Optional zero-DDL fallback sampler (Section 11), used only when the workload XE session
can't be deployed. Point-in-time snapshot of active requests, aggregated to one
fact_session_sample row per (login, program, host, db) per sample. Approximate -- misses
queries that complete between samples. dm_exec_requests.cpu_time is already milliseconds
(no unit conversion needed, unlike the microsecond DMVs in query_perf/workload).
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any

from src.collectors.base import Collector

_SOURCE_QUERY = """
SELECT
    s.login_name,
    s.program_name,
    s.host_name,
    DB_NAME(r.database_id) AS database_name,
    r.cpu_time,
    r.logical_reads
FROM sys.dm_exec_requests r
JOIN sys.dm_exec_sessions s ON r.session_id = s.session_id
WHERE s.is_user_process = 1 AND r.session_id <> @@SPID;
"""

_COLUMNS = (
    "source_instance",
    "sample_time_utc",
    "login_name",
    "program_name",
    "host_name",
    "database_name",
    "active_requests",
    "cpu_ms_inflight",
    "reads_inflight",
)

_UPSERT_SQL = """
MERGE dbo.fact_session_sample AS tgt
USING (SELECT ? AS source_instance, ? AS sample_time_utc, ? AS login_name, ? AS program_name,
              ? AS host_name, ? AS database_name, ? AS active_requests, ? AS cpu_ms_inflight,
              ? AS reads_inflight) AS src
ON tgt.source_instance = src.source_instance AND tgt.sample_time_utc = src.sample_time_utc
   AND tgt.login_name = src.login_name AND tgt.program_name = src.program_name
   AND tgt.host_name = src.host_name AND tgt.database_name = src.database_name
WHEN MATCHED THEN UPDATE SET
    active_requests = src.active_requests, cpu_ms_inflight = src.cpu_ms_inflight,
    reads_inflight = src.reads_inflight
WHEN NOT MATCHED THEN
    INSERT (source_instance, sample_time_utc, login_name, program_name, host_name, database_name,
            active_requests, cpu_ms_inflight, reads_inflight)
    VALUES (src.source_instance, src.sample_time_utc, src.login_name, src.program_name, src.host_name,
            src.database_name, src.active_requests, src.cpu_ms_inflight, src.reads_inflight);
"""


class SessionsCollector(Collector):
    task_name = "sessions"

    def source_query(self) -> str:
        return _SOURCE_QUERY

    def transform(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sample_time_utc = self._utcnow()
        groups: dict[tuple, dict[str, Any]] = defaultdict(lambda: {"active_requests": 0, "cpu_ms_inflight": 0, "reads_inflight": 0})
        for r in rows:
            key = (r["login_name"], r["program_name"], r["host_name"], r["database_name"])
            g = groups[key]
            g["active_requests"] += 1
            g["cpu_ms_inflight"] += r["cpu_time"] or 0
            g["reads_inflight"] += r["logical_reads"] or 0

        return [
            {
                "source_instance": self.source_instance,
                "sample_time_utc": sample_time_utc,
                "login_name": key[0],
                "program_name": key[1],
                "host_name": key[2],
                "database_name": key[3],
                **agg,
            }
            for key, agg in groups.items()
        ]

    def upsert_sql(self) -> str:
        return _UPSERT_SQL

    def columns(self) -> tuple[str, ...]:
        return _COLUMNS

    @staticmethod
    def _utcnow() -> dt.datetime:
        return dt.datetime.now(dt.timezone.utc)
