import datetime as dt

from src.collectors.storage import StorageCollector
from tests.conftest import load_fixture


def _rows_with_db_tag():
    # simulates what PerDatabaseCollector.fetch_rows() hands to transform(): the raw
    # source_query() columns plus a database_name tag (tested separately in
    # test_per_database_collector.py).
    return load_fixture("storage_partition_stats.json")


def test_transform_maps_columns_and_stamps_source_instance_and_date(monkeypatch):
    fixed_date = dt.date(2026, 7, 1)
    monkeypatch.setattr(StorageCollector, "_today", staticmethod(lambda: fixed_date))

    collector = StorageCollector(source_instance="PROD-SQL-01", config={})
    out = collector.transform(_rows_with_db_tag())

    assert out[0] == {
        "source_instance": "PROD-SQL-01",
        "snapshot_date": fixed_date,
        "database_name": "AppDb",
        "schema_name": "dbo",
        "table_name": "Orders",
        "row_count": 1500000,
        "data_kb": 204800,
        "index_kb": 51200,
        "unused_kb": 2048,
    }


def test_columns_match_upsert_placeholder_order():
    collector = StorageCollector(source_instance="PROD-SQL-01", config={})
    assert collector.columns() == (
        "source_instance",
        "snapshot_date",
        "database_name",
        "schema_name",
        "table_name",
        "row_count",
        "data_kb",
        "index_kb",
        "unused_kb",
    )
    sql = collector.upsert_sql()
    assert sql.count("?") == len(collector.columns())
    assert "MERGE dbo.fact_table_storage" in sql


def test_source_query_computes_index_and_unused_kb_from_partition_stats():
    collector = StorageCollector(source_instance="PROD-SQL-01", config={})
    query = collector.source_query()
    assert "sys.dm_db_partition_stats" in query
    assert "index_id IN (0, 1)" in query
    assert "AS data_kb" in query
    assert "AS index_kb" in query
    assert "AS unused_kb" in query
