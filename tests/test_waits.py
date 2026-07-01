import datetime as dt

from src.collectors.waits import WaitsCollector
from tests.conftest import load_fixture

CONFIG = {"wait_type_exclusions": ["SLEEP_TASK", "LAZYWRITER_SLEEP", "WAITFOR"]}


def _dict_rows():
    fixture = load_fixture("wait_stats.json")
    columns = ["wait_type", "wait_time_ms", "waiting_tasks_count"]
    return [dict(zip(columns, (r["wait_type"], r["wait_time_ms"], r["waiting_tasks_count"]), strict=True)) for r in fixture]


def test_transform_excludes_benign_wait_types():
    collector = WaitsCollector(source_instance="PROD-SQL-01", config=CONFIG)
    out = collector.transform(_dict_rows())

    wait_types = {row["wait_type"] for row in out}
    assert wait_types == {"PAGEIOLATCH_SH", "CXPACKET"}


def test_transform_stamps_one_snapshot_time_for_all_rows(monkeypatch):
    fixed_now = dt.datetime(2026, 7, 1, 0, 0, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(WaitsCollector, "_utcnow", staticmethod(lambda: fixed_now))

    collector = WaitsCollector(source_instance="PROD-SQL-01", config=CONFIG)
    out = collector.transform(_dict_rows())

    assert all(row["snapshot_time_utc"] == fixed_now for row in out)


def test_transform_maps_dmv_columns_to_fact_columns():
    collector = WaitsCollector(source_instance="PROD-SQL-01", config=CONFIG)
    out = collector.transform(_dict_rows())

    row = next(r for r in out if r["wait_type"] == "PAGEIOLATCH_SH")
    assert row["wait_ms_cum"] == 15000
    assert row["waiting_tasks_cum"] == 200
    assert row["source_instance"] == "PROD-SQL-01"


def test_columns_match_upsert_placeholder_order():
    collector = WaitsCollector(source_instance="PROD-SQL-01", config=CONFIG)
    assert collector.columns() == (
        "source_instance",
        "snapshot_time_utc",
        "wait_type",
        "wait_ms_cum",
        "waiting_tasks_cum",
    )
    sql = collector.upsert_sql()
    assert sql.count("?") == len(collector.columns())
    assert "MERGE dbo.fact_wait_stats" in sql


def test_source_query_reads_wait_stats_dmv():
    collector = WaitsCollector(source_instance="PROD-SQL-01", config=CONFIG)
    assert "sys.dm_os_wait_stats" in collector.source_query()
