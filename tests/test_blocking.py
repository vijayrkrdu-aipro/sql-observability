import datetime as dt

from src.collectors.blocking import BlockingCollector
from tests.conftest import load_fixture


def _dict_rows():
    return load_fixture("blocking_dmv.json")


def test_transform_maps_each_blocked_session_and_stamps_sample_time(monkeypatch):
    fixed_now = dt.datetime(2026, 7, 1, 0, 0, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(BlockingCollector, "_utcnow", staticmethod(lambda: fixed_now))

    collector = BlockingCollector(source_instance="PROD-SQL-01", config={})
    out = collector.transform(_dict_rows())

    assert len(out) == 2
    assert out[0] == {
        "source_instance": "PROD-SQL-01",
        "sample_time_utc": fixed_now,
        "session_id": 60,
        "blocking_session_id": 52,
        "wait_type": "LCK_M_X",
        "wait_time_ms": 4500,
        "wait_resource": "KEY: 5:72057594043170816 (a1b2c3d4e5f6)",
        "login_name": "DOMAIN\\svc_etl",
        "program_name": "SSIS-Nightly",
        "database_name": "AppDb",
    }
    assert out[1]["session_id"] == 61
    assert out[1]["blocking_session_id"] == 52  # both blocked by the same head-of-chain session


def test_columns_match_upsert_placeholder_order():
    collector = BlockingCollector(source_instance="PROD-SQL-01", config={})
    assert collector.columns() == (
        "source_instance",
        "sample_time_utc",
        "session_id",
        "blocking_session_id",
        "wait_type",
        "wait_time_ms",
        "wait_resource",
        "login_name",
        "program_name",
        "database_name",
    )
    sql = collector.upsert_sql()
    assert sql.count("?") == len(collector.columns())
    assert "MERGE dbo.fact_blocking_snapshot" in sql


def test_source_query_filters_to_blocked_sessions_only():
    collector = BlockingCollector(source_instance="PROD-SQL-01", config={})
    query = collector.source_query()
    assert "sys.dm_exec_requests" in query
    assert "r.blocking_session_id <> 0" in query
