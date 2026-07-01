"""Top queries without Query Store (SQL 2019) — Section 11.

Source = plan cache (sys.dm_exec_query_stats + sys.dm_exec_sql_text), aggregated to
query_hash grain. Captures top-N by CPU and top-N by logical reads in one query (UNION
on query_hash, so a query_hash in both lists collapses to one row); union also dedupes
identical rows since both branches aggregate the same underlying data per query_hash.
Times are microseconds in the DMVs -- divided by 1000 in SQL so ms conversion is
guaranteed correct even without a live instance to check against.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from src.collectors.base import Collector

_AGGREGATION = """
SELECT
    qs.query_hash,
    SUM(qs.execution_count)                                          AS exec_count,
    SUM(qs.total_worker_time)  / 1000.0                             AS total_cpu_ms,
    SUM(qs.total_worker_time)  / NULLIF(SUM(qs.execution_count),0) / 1000.0 AS avg_cpu_ms,
    SUM(qs.total_elapsed_time) / NULLIF(SUM(qs.execution_count),0) / 1000.0 AS avg_duration_ms,
    SUM(qs.total_logical_reads)/ NULLIF(SUM(qs.execution_count),0)          AS avg_logical_reads,
    MIN(SUBSTRING(st.text, (qs.statement_start_offset/2)+1,
        ((CASE qs.statement_end_offset WHEN -1 THEN DATALENGTH(st.text)
          ELSE qs.statement_end_offset END - qs.statement_start_offset)/2)+1)) AS query_sql_text
FROM sys.dm_exec_query_stats qs
CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) st
GROUP BY qs.query_hash
"""

_COLUMNS = (
    "source_instance",
    "snapshot_time_utc",
    "query_hash",
    "exec_count",
    "avg_cpu_ms",
    "avg_duration_ms",
    "avg_logical_reads",
    "total_cpu_ms",
    "query_sql_text",
)

_UPSERT_SQL = """
MERGE dbo.fact_query_perf AS tgt
USING (SELECT ? AS source_instance, ? AS snapshot_time_utc, ? AS query_hash, ? AS exec_count,
              ? AS avg_cpu_ms, ? AS avg_duration_ms, ? AS avg_logical_reads,
              ? AS total_cpu_ms, ? AS query_sql_text) AS src
ON tgt.source_instance = src.source_instance
   AND tgt.snapshot_time_utc = src.snapshot_time_utc
   AND tgt.query_hash = src.query_hash
WHEN MATCHED THEN UPDATE SET
    exec_count = src.exec_count, avg_cpu_ms = src.avg_cpu_ms, avg_duration_ms = src.avg_duration_ms,
    avg_logical_reads = src.avg_logical_reads, total_cpu_ms = src.total_cpu_ms,
    query_sql_text = src.query_sql_text
WHEN NOT MATCHED THEN
    INSERT (source_instance, snapshot_time_utc, query_hash, exec_count, avg_cpu_ms,
            avg_duration_ms, avg_logical_reads, total_cpu_ms, query_sql_text)
    VALUES (src.source_instance, src.snapshot_time_utc, src.query_hash, src.exec_count, src.avg_cpu_ms,
            src.avg_duration_ms, src.avg_logical_reads, src.total_cpu_ms, src.query_sql_text);
"""


class QueryPerfCollector(Collector):
    task_name = "query_perf"

    def source_query(self) -> str:
        top_n = int(self.config.get("query_perf", {}).get("top_n", 50))
        by_cpu = f"SELECT TOP ({top_n}) * FROM ({_AGGREGATION}) cpu_agg ORDER BY total_cpu_ms DESC"
        by_reads = (
            f"SELECT TOP ({top_n}) * FROM ({_AGGREGATION}) reads_agg ORDER BY avg_logical_reads DESC"
        )
        return f"{by_cpu}\nUNION\n{by_reads};"

    def transform(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        snapshot_time_utc = self._utcnow()
        seen: dict[Any, dict[str, Any]] = {}
        for r in rows:
            query_hash = r["query_hash"]
            if query_hash in seen:
                continue
            seen[query_hash] = {
                "source_instance": self.source_instance,
                "snapshot_time_utc": snapshot_time_utc,
                "query_hash": query_hash,
                "exec_count": r["exec_count"],
                "avg_cpu_ms": r["avg_cpu_ms"],
                "avg_duration_ms": r["avg_duration_ms"],
                "avg_logical_reads": r["avg_logical_reads"],
                "total_cpu_ms": r["total_cpu_ms"],
                "query_sql_text": r["query_sql_text"],  # None when the plan was evicted
            }
        return list(seen.values())

    def upsert_sql(self) -> str:
        return _UPSERT_SQL

    def columns(self) -> tuple[str, ...]:
        return _COLUMNS

    @staticmethod
    def _utcnow() -> dt.datetime:
        return dt.datetime.now(dt.timezone.utc)
