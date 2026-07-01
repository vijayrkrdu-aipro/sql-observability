"""Per-database table volume snapshot (Section 11). One row per table per day.

reserved/used/data page counts come from sys.dm_db_partition_stats (current-database
scoped, hence PerDatabaseCollector); index_kb/unused_kb are derived in SQL directly from
the aggregated page counts. row_count only counts index_id IN (0,1) (heap/clustered),
matching how SSMS reports table row counts (avoids double-counting nonclustered indexes).
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from src.collectors.base import PerDatabaseCollector

_SOURCE_QUERY = """
SELECT
    s.name AS schema_name,
    t.name AS table_name,
    SUM(CASE WHEN ps.index_id IN (0, 1) THEN ps.row_count ELSE 0 END) AS row_count,
    SUM(ps.in_row_data_page_count + ps.lob_used_page_count + ps.row_overflow_used_page_count) * 8 AS data_kb,
    (SUM(ps.used_page_count)
     - SUM(ps.in_row_data_page_count + ps.lob_used_page_count + ps.row_overflow_used_page_count)) * 8 AS index_kb,
    (SUM(ps.reserved_page_count) - SUM(ps.used_page_count)) * 8 AS unused_kb
FROM sys.dm_db_partition_stats ps
JOIN sys.tables t ON t.object_id = ps.object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE t.is_ms_shipped = 0
GROUP BY s.name, t.name;
"""

_COLUMNS = (
    "source_instance",
    "snapshot_date",
    "database_name",
    "schema_name",
    "table_name",
    "row_count",
    "data_kb",
    "index_kb",
    "unused_kb",
)

_UPSERT_SQL = """
MERGE dbo.fact_table_storage AS tgt
USING (SELECT ? AS source_instance, ? AS snapshot_date, ? AS database_name, ? AS schema_name,
              ? AS table_name, ? AS row_count, ? AS data_kb, ? AS index_kb, ? AS unused_kb) AS src
ON tgt.source_instance = src.source_instance AND tgt.snapshot_date = src.snapshot_date
   AND tgt.database_name = src.database_name AND tgt.schema_name = src.schema_name
   AND tgt.table_name = src.table_name
WHEN MATCHED THEN UPDATE SET
    row_count = src.row_count, data_kb = src.data_kb, index_kb = src.index_kb, unused_kb = src.unused_kb
WHEN NOT MATCHED THEN
    INSERT (source_instance, snapshot_date, database_name, schema_name, table_name,
            row_count, data_kb, index_kb, unused_kb)
    VALUES (src.source_instance, src.snapshot_date, src.database_name, src.schema_name, src.table_name,
            src.row_count, src.data_kb, src.index_kb, src.unused_kb);
"""


class StorageCollector(PerDatabaseCollector):
    task_name = "storage"

    def source_query(self) -> str:
        return _SOURCE_QUERY

    def transform(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        snapshot_date = self._today()
        return [
            {
                "source_instance": self.source_instance,
                "snapshot_date": snapshot_date,
                "database_name": r["database_name"],
                "schema_name": r["schema_name"],
                "table_name": r["table_name"],
                "row_count": r["row_count"],
                "data_kb": r["data_kb"],
                "index_kb": r["index_kb"],
                "unused_kb": r["unused_kb"],
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
