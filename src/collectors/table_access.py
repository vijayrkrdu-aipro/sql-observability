"""Per-day table access counts + patterns, per database (Section 11).

sys.dm_db_index_usage_stats is current-database scoped, hence PerDatabaseCollector.
Rolls all indexes of a table into one row (table grain). Stores CUMULATIVE counters --
per-day deltas are computed in rpt.table_access_daily, which also handles counter resets
on restart. Run at a consistent daily time (scheduler's job, not this code) so consecutive
snapshots delta cleanly.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from src.collectors.base import PerDatabaseCollector

_SOURCE_QUERY = """
SELECT
    s.name AS schema_name,
    o.name AS table_name,
    SUM(ius.user_seeks)   AS seeks_cum,
    SUM(ius.user_scans)   AS scans_cum,
    SUM(ius.user_lookups) AS lookups_cum,
    SUM(ius.user_updates) AS updates_cum,
    MAX(row_last_read.v)  AS last_user_read_utc,
    MAX(ius.last_user_update) AS last_user_update_utc
FROM sys.dm_db_index_usage_stats ius
JOIN sys.objects o ON o.object_id = ius.object_id
JOIN sys.schemas s ON s.schema_id = o.schema_id
CROSS APPLY (
    SELECT MAX(v) AS v FROM (VALUES (ius.last_user_seek), (ius.last_user_scan), (ius.last_user_lookup)) AS t(v)
) row_last_read
WHERE ius.database_id = DB_ID() AND o.type = 'U' AND o.is_ms_shipped = 0
GROUP BY s.name, o.name;
"""

_COLUMNS = (
    "source_instance",
    "snapshot_date",
    "database_name",
    "schema_name",
    "table_name",
    "seeks_cum",
    "scans_cum",
    "lookups_cum",
    "updates_cum",
    "last_user_read_utc",
    "last_user_update_utc",
)

_UPSERT_SQL = """
MERGE dbo.fact_table_usage AS tgt
USING (SELECT ? AS source_instance, ? AS snapshot_date, ? AS database_name, ? AS schema_name,
              ? AS table_name, ? AS seeks_cum, ? AS scans_cum, ? AS lookups_cum, ? AS updates_cum,
              ? AS last_user_read_utc, ? AS last_user_update_utc) AS src
ON tgt.source_instance = src.source_instance AND tgt.snapshot_date = src.snapshot_date
   AND tgt.database_name = src.database_name AND tgt.schema_name = src.schema_name
   AND tgt.table_name = src.table_name
WHEN MATCHED THEN UPDATE SET
    seeks_cum = src.seeks_cum, scans_cum = src.scans_cum, lookups_cum = src.lookups_cum,
    updates_cum = src.updates_cum, last_user_read_utc = src.last_user_read_utc,
    last_user_update_utc = src.last_user_update_utc
WHEN NOT MATCHED THEN
    INSERT (source_instance, snapshot_date, database_name, schema_name, table_name,
            seeks_cum, scans_cum, lookups_cum, updates_cum, last_user_read_utc, last_user_update_utc)
    VALUES (src.source_instance, src.snapshot_date, src.database_name, src.schema_name, src.table_name,
            src.seeks_cum, src.scans_cum, src.lookups_cum, src.updates_cum,
            src.last_user_read_utc, src.last_user_update_utc);
"""


class TableAccessCollector(PerDatabaseCollector):
    task_name = "table_access"

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
                "seeks_cum": r["seeks_cum"],
                "scans_cum": r["scans_cum"],
                "lookups_cum": r["lookups_cum"],
                "updates_cum": r["updates_cum"],
                "last_user_read_utc": r["last_user_read_utc"],
                "last_user_update_utc": r["last_user_update_utc"],
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
