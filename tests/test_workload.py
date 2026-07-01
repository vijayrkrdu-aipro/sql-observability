import datetime as dt

from src.collectors.workload import WorkloadCollector, parse_xe_event
from tests.conftest import FakeConnection, load_fixture

_UTC = dt.timezone.utc


def _events():
    return load_fixture("workload_xe_events.json")


def test_parse_xe_event_extracts_data_and_action_fields():
    event = parse_xe_event(_events()[0]["event_data"])

    assert event["timestamp"] == dt.datetime(2026, 7, 1, 0, 0, 0, tzinfo=_UTC)
    assert event["cpu_time"] == 50000
    assert event["duration"] == 120000
    assert event["logical_reads"] == 200
    assert event["writes"] == 1
    assert event["row_count"] == 10
    assert event["login_name"] == "DOMAIN\\svc_app"
    assert event["program_name"] == "MyApp"
    assert event["host_name"] == "APPHOST01"
    assert event["database_name"] == "AppDb"


def test_transform_aggregates_by_attribution_key_with_no_watermark():
    collector = WorkloadCollector(source_instance="PROD-SQL-01", config={})
    out = collector.transform(_events())

    assert len(out) == 2  # svc_app/MyApp group + svc_etl/SSIS-Nightly group

    app_row = next(r for r in out if r["login_name"] == "DOMAIN\\svc_app")
    assert app_row["exec_count"] == 2
    assert app_row["total_cpu_ms"] == 80.0  # (50000 + 30000) / 1000
    assert app_row["total_duration_ms"] == 200.0  # (120000 + 80000) / 1000
    assert app_row["total_logical_reads"] == 300
    assert app_row["total_writes"] == 1
    assert app_row["total_rows"] == 15

    etl_row = next(r for r in out if r["login_name"] == "DOMAIN\\svc_etl")
    assert etl_row["exec_count"] == 1
    assert etl_row["total_cpu_ms"] == 2000.0

    # window_start_utc defaults to the earliest event timestamp when there's no watermark yet
    assert all(r["window_start_utc"] == dt.datetime(2026, 6, 30, 23, 59, 0, tzinfo=_UTC) for r in out)


def test_transform_skips_events_at_or_under_watermark():
    collector = WorkloadCollector(source_instance="PROD-SQL-01", config={})
    collector._watermark = dt.datetime(2026, 7, 1, 0, 0, 0, tzinfo=_UTC)  # == first event's timestamp

    out = collector.transform(_events())

    # event 1 (== watermark) and event 3 (before watermark) excluded; only event 2 remains
    assert len(out) == 1
    assert out[0]["login_name"] == "DOMAIN\\svc_app"
    assert out[0]["exec_count"] == 1
    assert out[0]["total_cpu_ms"] == 30.0
    assert out[0]["window_start_utc"] == collector._watermark


def test_transform_returns_empty_when_all_events_filtered_out():
    collector = WorkloadCollector(source_instance="PROD-SQL-01", config={})
    collector._watermark = dt.datetime(2026, 7, 1, 0, 0, 5, tzinfo=_UTC)  # after every event

    assert collector.transform(_events()) == []


def test_get_watermark_reads_max_started_at_for_successful_runs():
    fixed = dt.datetime(2026, 6, 30, 12, 0, 0, tzinfo=_UTC)
    repo_conn = FakeConnection(rows=[(fixed,)], columns=["watermark"])
    collector = WorkloadCollector(source_instance="PROD-SQL-01", config={})

    watermark = collector._get_watermark(repo_conn)

    assert watermark == fixed
    query, params = repo_conn.cursor_obj.executed[0]
    assert "dbo.collection_run" in query
    assert params == ("PROD-SQL-01",)


def test_get_watermark_returns_none_on_first_run():
    repo_conn = FakeConnection(rows=[(None,)], columns=["watermark"])
    collector = WorkloadCollector(source_instance="PROD-SQL-01", config={})

    assert collector._get_watermark(repo_conn) is None


def test_columns_match_upsert_placeholder_order():
    collector = WorkloadCollector(source_instance="PROD-SQL-01", config={})
    assert collector.columns() == (
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
    sql = collector.upsert_sql()
    assert sql.count("?") == len(collector.columns())
    assert "MERGE dbo.fact_workload" in sql
    assert "attribution_hash" in sql  # matched via recomputed hash, never inserted directly
    assert "HASHBYTES" in sql


def test_source_query_uses_configured_xe_file_glob():
    collector = WorkloadCollector(
        source_instance="PROD-SQL-01",
        config={"workload": {"xe_file_glob": "Custom_Workload*.xel"}},
    )
    query = collector.source_query()
    assert "sys.fn_xe_file_target_read_file" in query
    assert "Custom_Workload*.xel" in query


def test_source_query_defaults_glob_when_unconfigured():
    collector = WorkloadCollector(source_instance="PROD-SQL-01", config={})
    assert "Observability_Workload*.xel" in collector.source_query()
