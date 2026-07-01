"""Per-database-file IO stats (Phase 4 -- Section 16's top deferred extension point:
"is storage the bottleneck"). sys.dm_io_virtual_file_stats(NULL, NULL) is server-wide (all
databases/files in one call), so unlike storage.py/index_ops.py/table_access.py this is a
plain Collector, no per-database USE looping needed. Raw cumulative since restart -- same
pattern as waits.py; per-window avg-latency-per-IO is computed in rpt.io_latency_deltas.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from src.collectors.base import Collector

_SOURCE_QUERY = """
SELECT
    DB_NAME(vfs.database_id) AS database_name,
    mf.name        AS logical_file_name,
    mf.type_desc   AS file_type,
    vfs.num_of_reads         AS reads_cum,
    vfs.num_of_bytes_read    AS bytes_read_cum,
    vfs.io_stall_read_ms     AS read_stall_ms_cum,
    vfs.num_of_writes        AS writes_cum,
    vfs.num_of_bytes_written AS bytes_written_cum,
    vfs.io_stall_write_ms    AS write_stall_ms_cum,
    vfs.size_on_disk_bytes   AS size_on_disk_bytes
FROM sys.dm_io_virtual_file_stats(NULL, NULL) vfs
JOIN sys.master_files mf ON mf.database_id = vfs.database_id AND mf.file_id = vfs.file_id
WHERE vfs.database_id > 4;
"""

_COLUMNS = (
    "source_instance",
    "snapshot_time_utc",
    "database_name",
    "logical_file_name",
    "file_type",
    "reads_cum",
    "bytes_read_cum",
    "read_stall_ms_cum",
    "writes_cum",
    "bytes_written_cum",
    "write_stall_ms_cum",
    "size_on_disk_bytes",
)

_UPSERT_SQL = """
MERGE dbo.fact_io_latency AS tgt
USING (SELECT ? AS source_instance, ? AS snapshot_time_utc, ? AS database_name, ? AS logical_file_name,
              ? AS file_type, ? AS reads_cum, ? AS bytes_read_cum, ? AS read_stall_ms_cum,
              ? AS writes_cum, ? AS bytes_written_cum, ? AS write_stall_ms_cum,
              ? AS size_on_disk_bytes) AS src
ON tgt.source_instance = src.source_instance AND tgt.snapshot_time_utc = src.snapshot_time_utc
   AND tgt.database_name = src.database_name AND tgt.logical_file_name = src.logical_file_name
WHEN MATCHED THEN UPDATE SET
    file_type = src.file_type, reads_cum = src.reads_cum, bytes_read_cum = src.bytes_read_cum,
    read_stall_ms_cum = src.read_stall_ms_cum, writes_cum = src.writes_cum,
    bytes_written_cum = src.bytes_written_cum, write_stall_ms_cum = src.write_stall_ms_cum,
    size_on_disk_bytes = src.size_on_disk_bytes
WHEN NOT MATCHED THEN
    INSERT (source_instance, snapshot_time_utc, database_name, logical_file_name, file_type,
            reads_cum, bytes_read_cum, read_stall_ms_cum, writes_cum, bytes_written_cum,
            write_stall_ms_cum, size_on_disk_bytes)
    VALUES (src.source_instance, src.snapshot_time_utc, src.database_name, src.logical_file_name,
            src.file_type, src.reads_cum, src.bytes_read_cum, src.read_stall_ms_cum, src.writes_cum,
            src.bytes_written_cum, src.write_stall_ms_cum, src.size_on_disk_bytes);
"""


class IoLatencyCollector(Collector):
    task_name = "io_latency"

    def source_query(self) -> str:
        return _SOURCE_QUERY

    def transform(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        snapshot_time_utc = self._utcnow()
        return [
            {
                "source_instance": self.source_instance,
                "snapshot_time_utc": snapshot_time_utc,
                "database_name": r["database_name"],
                "logical_file_name": r["logical_file_name"],
                "file_type": r["file_type"],
                "reads_cum": r["reads_cum"],
                "bytes_read_cum": r["bytes_read_cum"],
                "read_stall_ms_cum": r["read_stall_ms_cum"],
                "writes_cum": r["writes_cum"],
                "bytes_written_cum": r["bytes_written_cum"],
                "write_stall_ms_cum": r["write_stall_ms_cum"],
                "size_on_disk_bytes": r["size_on_disk_bytes"],
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
