"""Login + workload-type attribution via Extended Events (Section 11 -- the headline
feature). Reads the Observability_Workload XE session's .xel files through
sys.fn_xe_file_target_read_file (VIEW SERVER STATE only; the session itself is deployed
one-time by a DBA -- see sql/workload_attribution.sql PART B). This collector never
creates or alters the session, only reads it.

Incremental: the watermark is the started_at_utc of the last successful 'workload' run for
this source_instance (read from dbo.collection_run on the repo), so events at/under it are
skipped. Events are aggregated by (login_name, program_name, host_name, database_name) into
one fact_workload row per group per run; workload *category* classification happens later in
rpt.workload_by_category, not here.
"""
from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import Any

from src import db
from src.collectors.base import Collector

_COLUMNS = (
    "source_instance",
    "window_start_utc",
    "login_name",
    "program_name",
    "host_name",
    "database_name",
    "exec_count",
    "total_cpu_ms",
    "total_duration_ms",
    "total_logical_reads",
    "total_writes",
    "total_rows",
)

# attribution_hash is a computed/persisted column on fact_workload (see
# workload_attribution.sql) -- recomputed here identically so MERGE can match on it.
_UPSERT_SQL = """
MERGE dbo.fact_workload AS tgt
USING (SELECT ? AS source_instance, ? AS window_start_utc, ? AS login_name, ? AS program_name,
              ? AS host_name, ? AS database_name, ? AS exec_count, ? AS total_cpu_ms,
              ? AS total_duration_ms, ? AS total_logical_reads, ? AS total_writes, ? AS total_rows) AS src
ON tgt.source_instance = src.source_instance
   AND tgt.window_start_utc = src.window_start_utc
   AND tgt.attribution_hash = CONVERT(BINARY(32), HASHBYTES('SHA2_256',
       CONCAT(src.login_name, '|', src.program_name, '|', src.host_name, '|', src.database_name)))
WHEN MATCHED THEN UPDATE SET
    exec_count = src.exec_count, total_cpu_ms = src.total_cpu_ms, total_duration_ms = src.total_duration_ms,
    total_logical_reads = src.total_logical_reads, total_writes = src.total_writes, total_rows = src.total_rows
WHEN NOT MATCHED THEN
    INSERT (source_instance, window_start_utc, login_name, program_name, host_name, database_name,
            exec_count, total_cpu_ms, total_duration_ms, total_logical_reads, total_writes, total_rows)
    VALUES (src.source_instance, src.window_start_utc, src.login_name, src.program_name, src.host_name,
            src.database_name, src.exec_count, src.total_cpu_ms, src.total_duration_ms,
            src.total_logical_reads, src.total_writes, src.total_rows);
"""

_WATERMARK_QUERY = """
SELECT MAX(started_at_utc) AS watermark FROM dbo.collection_run
WHERE source_instance = ? AND task = 'workload' AND status = 'success';
"""


def parse_xe_event(event_data_xml: str) -> dict[str, Any]:
    """Parse one <event> XML blob from sys.fn_xe_file_target_read_file into a flat dict."""
    root = ET.fromstring(event_data_xml)
    data = {el.attrib["name"]: el.findtext("value") for el in root.findall("data")}
    actions = {el.attrib["name"]: el.findtext("value") for el in root.findall("action")}
    return {
        "timestamp": dt.datetime.fromisoformat(root.attrib["timestamp"].replace("Z", "+00:00")),
        "cpu_time": int(data.get("cpu_time") or 0),
        "duration": int(data.get("duration") or 0),
        "logical_reads": int(data.get("logical_reads") or 0),
        "writes": int(data.get("writes") or 0),
        "row_count": int(data.get("row_count") or 0),
        "login_name": actions.get("server_principal_name"),
        "program_name": actions.get("client_app_name"),
        "host_name": actions.get("client_hostname"),
        "database_name": actions.get("database_name"),
    }


class WorkloadCollector(Collector):
    task_name = "workload"

    def __init__(self, source_instance: str, config: dict[str, Any]):
        super().__init__(source_instance, config)
        self._watermark: dt.datetime | None = None

    def run(self, source_conn: Any, repo_conn: Any, dry_run: bool = False) -> int:
        self._watermark = self._get_watermark(repo_conn)
        return super().run(source_conn, repo_conn, dry_run)

    def _get_watermark(self, repo_conn: Any) -> dt.datetime | None:
        rows = db.execute(repo_conn, _WATERMARK_QUERY, (self.source_instance,))
        watermark = rows[0]["watermark"] if rows else None
        return watermark

    def source_query(self) -> str:
        glob = self.config.get("workload", {}).get("xe_file_glob", "Observability_Workload*.xel")
        escaped = glob.replace("'", "''")
        return f"SELECT event_data FROM sys.fn_xe_file_target_read_file(N'{escaped}', NULL, NULL, NULL);"

    def transform(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        events = [parse_xe_event(r["event_data"]) for r in rows]
        if self._watermark is not None:
            events = [e for e in events if e["timestamp"] > self._watermark]
        if not events:
            return []

        window_start_utc = self._watermark if self._watermark is not None else min(e["timestamp"] for e in events)

        groups: dict[tuple, dict[str, Any]] = defaultdict(
            lambda: {
                "exec_count": 0,
                "total_cpu_ms": 0.0,
                "total_duration_ms": 0.0,
                "total_logical_reads": 0,
                "total_writes": 0,
                "total_rows": 0,
            }
        )
        for e in events:
            key = (e["login_name"], e["program_name"], e["host_name"], e["database_name"])
            g = groups[key]
            g["exec_count"] += 1
            g["total_cpu_ms"] += e["cpu_time"] / 1000.0
            g["total_duration_ms"] += e["duration"] / 1000.0
            g["total_logical_reads"] += e["logical_reads"]
            g["total_writes"] += e["writes"]
            g["total_rows"] += e["row_count"]

        return [
            {
                "source_instance": self.source_instance,
                "window_start_utc": window_start_utc,
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
