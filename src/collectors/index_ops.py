"""Missing + unused index opportunities, per database, one snapshot per day (Section 11).

Both DMV families are current-database scoped, hence PerDatabaseCollector. The two pulls
are UNIONed in one query per database, tagged by `kind` so fact_index_ops carries both
in the same shape: missing rows populate impact_score/detail (reads/writes NULL); unused
rows populate reads/writes (impact_score/detail NULL). Usage counters reset on restart --
this is an annotated snapshot, never a delta.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from src.collectors.base import PerDatabaseCollector

_SOURCE_QUERY = """
SELECT
    'missing' AS kind,
    OBJECT_SCHEMA_NAME(mid.object_id) + '.' + OBJECT_NAME(mid.object_id) AS object_name,
    migs.avg_total_user_cost * migs.avg_user_impact * (migs.user_seeks + migs.user_scans) AS impact_score,
    CAST(NULL AS BIGINT) AS reads,
    CAST(NULL AS BIGINT) AS writes,
    'EQUALITY: ' + ISNULL(mid.equality_columns, '') +
    ' INEQUALITY: ' + ISNULL(mid.inequality_columns, '') +
    ' INCLUDED: ' + ISNULL(mid.included_columns, '') AS detail
FROM sys.dm_db_missing_index_details mid
JOIN sys.dm_db_missing_index_groups mig ON mig.index_handle = mid.index_handle
JOIN sys.dm_db_missing_index_group_stats migs ON migs.group_handle = mig.index_group_handle
WHERE mid.database_id = DB_ID()

UNION ALL

SELECT
    'unused' AS kind,
    OBJECT_SCHEMA_NAME(ius.object_id) + '.' + OBJECT_NAME(ius.object_id) + '.' + i.name AS object_name,
    CAST(NULL AS DECIMAL(18, 2)) AS impact_score,
    (ius.user_seeks + ius.user_scans + ius.user_lookups) AS reads,
    ius.user_updates AS writes,
    CAST(NULL AS NVARCHAR(MAX)) AS detail
FROM sys.dm_db_index_usage_stats ius
JOIN sys.indexes i ON i.object_id = ius.object_id AND i.index_id = ius.index_id
WHERE ius.database_id = DB_ID()
  AND i.name IS NOT NULL
  AND (ius.user_seeks + ius.user_scans + ius.user_lookups) = 0
  AND ius.user_updates > 0;
"""

_COLUMNS = (
    "source_instance",
    "snapshot_date",
    "database_name",
    "kind",
    "object_name",
    "impact_score",
    "reads",
    "writes",
    "detail",
)

_UPSERT_SQL = """
MERGE dbo.fact_index_ops AS tgt
USING (SELECT ? AS source_instance, ? AS snapshot_date, ? AS database_name, ? AS kind,
              ? AS object_name, ? AS impact_score, ? AS reads, ? AS writes, ? AS detail) AS src
ON tgt.source_instance = src.source_instance AND tgt.snapshot_date = src.snapshot_date
   AND tgt.database_name = src.database_name AND tgt.kind = src.kind
   AND tgt.object_name = src.object_name
WHEN MATCHED THEN UPDATE SET
    impact_score = src.impact_score, reads = src.reads, writes = src.writes, detail = src.detail
WHEN NOT MATCHED THEN
    INSERT (source_instance, snapshot_date, database_name, kind, object_name, impact_score, reads, writes, detail)
    VALUES (src.source_instance, src.snapshot_date, src.database_name, src.kind, src.object_name,
            src.impact_score, src.reads, src.writes, src.detail);
"""


class IndexOpsCollector(PerDatabaseCollector):
    task_name = "index_ops"

    def source_query(self) -> str:
        return _SOURCE_QUERY

    def transform(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        snapshot_date = self._today()
        return [
            {
                "source_instance": self.source_instance,
                "snapshot_date": snapshot_date,
                "database_name": r["database_name"],
                "kind": r["kind"],
                "object_name": r["object_name"],
                "impact_score": r["impact_score"],
                "reads": r["reads"],
                "writes": r["writes"],
                "detail": r["detail"],
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
