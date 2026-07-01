import datetime as dt

from src.collectors.deadlocks import DeadlocksCollector, parse_deadlock_events
from tests.conftest import FakeConnection, load_fixture

_UTC = dt.timezone.utc


def _target_xml():
    return load_fixture("deadlock_ring_buffer.json")["target_xml"]


def test_parse_deadlock_events_ignores_non_deadlock_events():
    events = parse_deadlock_events(_target_xml())
    assert len(events) == 2  # the wait_info decoy event is excluded


def test_parse_deadlock_events_extracts_victim_and_resources():
    events = parse_deadlock_events(_target_xml())
    first = events[0]

    assert first["event_time_utc"] == dt.datetime(2026, 7, 1, 0, 0, 0, tzinfo=_UTC)
    assert first["victim_session_id"] == 52
    assert first["victim_login"] == "DOMAIN\\svc_app"
    assert first["victim_program"] == "MyApp"
    assert first["process_count"] == 2
    assert first["resource_summary"] == "keylock:AppDb.dbo.Orders"
    assert "<deadlock>" in first["deadlock_graph_xml"]


def test_transform_returns_empty_list_when_no_rows():
    collector = DeadlocksCollector(source_instance="PROD-SQL-01", config={})
    assert collector.transform([]) == []


def test_transform_maps_all_events_with_no_watermark():
    collector = DeadlocksCollector(source_instance="PROD-SQL-01", config={})
    out = collector.transform([{"target_xml": _target_xml()}])

    assert len(out) == 2
    assert out[0]["source_instance"] == "PROD-SQL-01"
    assert out[0]["victim_session_id"] == 52
    assert out[1]["victim_session_id"] == 70


def test_transform_skips_events_at_or_under_watermark():
    collector = DeadlocksCollector(source_instance="PROD-SQL-01", config={})
    collector._watermark = dt.datetime(2026, 7, 1, 0, 0, 0, tzinfo=_UTC)  # == first event's timestamp

    out = collector.transform([{"target_xml": _target_xml()}])

    assert out == []  # both events are at/under the watermark


def test_get_watermark_reads_max_started_at_for_successful_deadlock_runs():
    fixed = dt.datetime(2026, 6, 30, 12, 0, 0, tzinfo=_UTC)
    repo_conn = FakeConnection(rows=[(fixed,)], columns=["watermark"])
    collector = DeadlocksCollector(source_instance="PROD-SQL-01", config={})

    assert collector._get_watermark(repo_conn) == fixed
    query, params = repo_conn.cursor_obj.executed[0]
    assert "task = 'deadlocks'" in query
    assert params == ("PROD-SQL-01",)


def test_columns_match_upsert_placeholder_order():
    collector = DeadlocksCollector(source_instance="PROD-SQL-01", config={})
    assert collector.columns() == (
        "source_instance",
        "event_time_utc",
        "victim_session_id",
        "victim_login",
        "victim_program",
        "process_count",
        "resource_summary",
        "deadlock_graph_xml",
    )
    sql = collector.upsert_sql()
    assert sql.count("?") == len(collector.columns())
    assert "MERGE dbo.fact_deadlock" in sql


def test_source_query_reads_system_health_ring_buffer():
    collector = DeadlocksCollector(source_instance="PROD-SQL-01", config={})
    query = collector.source_query()
    assert "sys.dm_xe_session_targets" in query
    assert "system_health" in query
    assert "ring_buffer" in query
