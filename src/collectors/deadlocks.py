"""Deadlock events read from the built-in system_health Extended Events session (Phase 4).
Zero deployment, unlike Observability_Workload -- system_health runs by default on every SQL
Server instance and needs no ALTER ANY EVENT SESSION. Only VIEW SERVER STATE required.

Caveat: system_health's default target is a RING_BUFFER (in-memory, capped size), not a file
-- on a very busy server it can roll over and evict deadlock events before this collector
ever reads them. Same "best-effort snapshot, not a complete history" caveat as query_perf.py's
plan cache. Incremental via a collection_run-backed watermark, same pattern as workload.py.
"""
from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from typing import Any

from src import db
from src.collectors.base import Collector

_SOURCE_QUERY = """
SELECT CAST(t.target_data AS NVARCHAR(MAX)) AS target_xml
FROM sys.dm_xe_session_targets t
JOIN sys.dm_xe_sessions s ON s.address = t.event_session_address
WHERE s.name = 'system_health' AND t.target_name = 'ring_buffer';
"""

_COLUMNS = (
    "source_instance",
    "event_time_utc",
    "victim_session_id",
    "victim_login",
    "victim_program",
    "process_count",
    "resource_summary",
    "deadlock_graph_xml",
)

_UPSERT_SQL = """
MERGE dbo.fact_deadlock AS tgt
USING (SELECT ? AS source_instance, ? AS event_time_utc, ? AS victim_session_id, ? AS victim_login,
              ? AS victim_program, ? AS process_count, ? AS resource_summary,
              ? AS deadlock_graph_xml) AS src
ON tgt.source_instance = src.source_instance AND tgt.event_time_utc = src.event_time_utc
WHEN MATCHED THEN UPDATE SET
    victim_session_id = src.victim_session_id, victim_login = src.victim_login,
    victim_program = src.victim_program, process_count = src.process_count,
    resource_summary = src.resource_summary, deadlock_graph_xml = src.deadlock_graph_xml
WHEN NOT MATCHED THEN
    INSERT (source_instance, event_time_utc, victim_session_id, victim_login, victim_program,
            process_count, resource_summary, deadlock_graph_xml)
    VALUES (src.source_instance, src.event_time_utc, src.victim_session_id, src.victim_login,
            src.victim_program, src.process_count, src.resource_summary, src.deadlock_graph_xml);
"""

_WATERMARK_QUERY = """
SELECT MAX(started_at_utc) AS watermark FROM dbo.collection_run
WHERE source_instance = ? AND task = 'deadlocks' AND status = 'success';
"""


def _parse_one_deadlock(event_el: ET.Element) -> dict[str, Any]:
    timestamp = dt.datetime.fromisoformat(event_el.attrib["timestamp"].replace("Z", "+00:00"))
    deadlock = event_el.find("data/value/deadlock")

    victim_ids = {vp.attrib["id"] for vp in deadlock.findall("victim-list/victimProcess")}
    processes = deadlock.findall("process-list/process")

    victim_session_id: int | None = None
    victim_login: str | None = None
    victim_program: str | None = None
    for p in processes:
        if p.attrib.get("id") in victim_ids:
            spid = p.attrib.get("spid")
            victim_session_id = int(spid) if spid else None
            victim_login = p.attrib.get("loginname")
            victim_program = p.attrib.get("clientapp")
            break

    resources = []
    for res in deadlock.findall("resource-list/*"):
        object_name = res.attrib.get("objectname")
        resources.append(f"{res.tag}:{object_name}" if object_name else res.tag)

    return {
        "event_time_utc": timestamp,
        "victim_session_id": victim_session_id,
        "victim_login": victim_login,
        "victim_program": victim_program,
        "process_count": len(processes),
        "resource_summary": ", ".join(resources) if resources else None,
        "deadlock_graph_xml": ET.tostring(deadlock, encoding="unicode"),
    }


def parse_deadlock_events(target_xml: str) -> list[dict[str, Any]]:
    """Extract every xml_deadlock_report event from a system_health ring_buffer XML dump.
    The ring buffer holds many other event types (wait_info, sp_server_diagnostics, ...);
    only xml_deadlock_report is relevant here.
    """
    root = ET.fromstring(target_xml)
    events = root.findall(".//event[@name='xml_deadlock_report']")
    return [_parse_one_deadlock(e) for e in events]


class DeadlocksCollector(Collector):
    task_name = "deadlocks"

    def __init__(self, source_instance: str, config: dict[str, Any]):
        super().__init__(source_instance, config)
        self._watermark: dt.datetime | None = None

    def run(self, source_conn: Any, repo_conn: Any, dry_run: bool = False) -> int:
        self._watermark = self._get_watermark(repo_conn)
        return super().run(source_conn, repo_conn, dry_run)

    def _get_watermark(self, repo_conn: Any) -> dt.datetime | None:
        rows = db.execute(repo_conn, _WATERMARK_QUERY, (self.source_instance,))
        return rows[0]["watermark"] if rows else None

    def source_query(self) -> str:
        return _SOURCE_QUERY

    def transform(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            return []
        events = parse_deadlock_events(rows[0]["target_xml"])
        if self._watermark is not None:
            events = [e for e in events if e["event_time_utc"] > self._watermark]

        return [
            {
                "source_instance": self.source_instance,
                "event_time_utc": e["event_time_utc"],
                "victim_session_id": e["victim_session_id"],
                "victim_login": e["victim_login"],
                "victim_program": e["victim_program"],
                "process_count": e["process_count"],
                "resource_summary": e["resource_summary"],
                "deadlock_graph_xml": e["deadlock_graph_xml"],
            }
            for e in events
        ]

    def upsert_sql(self) -> str:
        return _UPSERT_SQL

    def columns(self) -> tuple[str, ...]:
        return _COLUMNS
