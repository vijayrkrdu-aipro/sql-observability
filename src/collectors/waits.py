"""Cumulative wait stats (Section 11). Raw cumulative snapshot, one snapshot_time_utc
per run; deltas are computed in rpt.wait_deltas. Benign waits excluded via config.yaml
`wait_type_exclusions` (filtered in Python, not baked into the SQL text).
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from src.collectors.base import Collector

_SOURCE_QUERY = "SELECT wait_type, wait_time_ms, waiting_tasks_count FROM sys.dm_os_wait_stats;"

_COLUMNS = ("source_instance", "snapshot_time_utc", "wait_type", "wait_ms_cum", "waiting_tasks_cum")

_UPSERT_SQL = """
MERGE dbo.fact_wait_stats AS tgt
USING (SELECT ? AS source_instance, ? AS snapshot_time_utc, ? AS wait_type,
              ? AS wait_ms_cum, ? AS waiting_tasks_cum) AS src
ON tgt.source_instance = src.source_instance
   AND tgt.snapshot_time_utc = src.snapshot_time_utc
   AND tgt.wait_type = src.wait_type
WHEN MATCHED THEN UPDATE SET
    wait_ms_cum = src.wait_ms_cum, waiting_tasks_cum = src.waiting_tasks_cum
WHEN NOT MATCHED THEN
    INSERT (source_instance, snapshot_time_utc, wait_type, wait_ms_cum, waiting_tasks_cum)
    VALUES (src.source_instance, src.snapshot_time_utc, src.wait_type, src.wait_ms_cum, src.waiting_tasks_cum);
"""


class WaitsCollector(Collector):
    task_name = "waits"

    def source_query(self) -> str:
        return _SOURCE_QUERY

    def transform(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        exclusions = set(self.config.get("wait_type_exclusions", []))
        snapshot_time_utc = self._utcnow()
        return [
            {
                "source_instance": self.source_instance,
                "snapshot_time_utc": snapshot_time_utc,
                "wait_type": r["wait_type"],
                "wait_ms_cum": r["wait_time_ms"],
                "waiting_tasks_cum": r["waiting_tasks_count"],
            }
            for r in rows
            if r["wait_type"] not in exclusions
        ]

    def upsert_sql(self) -> str:
        return _UPSERT_SQL

    def columns(self) -> tuple[str, ...]:
        return _COLUMNS

    @staticmethod
    def _utcnow() -> dt.datetime:
        return dt.datetime.now(dt.timezone.utc)
