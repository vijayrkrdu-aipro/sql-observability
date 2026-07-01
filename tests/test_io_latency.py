import datetime as dt

from src.collectors.io_latency import IoLatencyCollector
from tests.conftest import load_fixture


def _dict_rows():
    return load_fixture("io_latency_dmv.json")


def test_transform_maps_columns_and_stamps_snapshot_time(monkeypatch):
    fixed_now = dt.datetime(2026, 7, 1, 0, 0, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(IoLatencyCollector, "_utcnow", staticmethod(lambda: fixed_now))

    collector = IoLatencyCollector(source_instance="PROD-SQL-01", config={})
    out = collector.transform(_dict_rows())

    assert out[0] == {
        "source_instance": "PROD-SQL-01",
        "snapshot_time_utc": fixed_now,
        "database_name": "AppDb",
        "logical_file_name": "AppDb_Data",
        "file_type": "ROWS",
        "reads_cum": 5000000,
        "bytes_read_cum": 40960000000,
        "read_stall_ms_cum": 120000,
        "writes_cum": 2000000,
        "bytes_written_cum": 10240000000,
        "write_stall_ms_cum": 60000,
        "size_on_disk_bytes": 214748364800,
    }
    assert out[1]["file_type"] == "LOG"
    assert all(r["snapshot_time_utc"] == fixed_now for r in out)


def test_columns_match_upsert_placeholder_order():
    collector = IoLatencyCollector(source_instance="PROD-SQL-01", config={})
    assert collector.columns() == (
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
    sql = collector.upsert_sql()
    assert sql.count("?") == len(collector.columns())
    assert "MERGE dbo.fact_io_latency" in sql


def test_source_query_is_server_wide_no_per_database_looping():
    collector = IoLatencyCollector(source_instance="PROD-SQL-01", config={})
    query = collector.source_query()
    assert "sys.dm_io_virtual_file_stats(NULL, NULL)" in query
    assert "sys.master_files" in query
    assert "vfs.database_id > 4" in query
