import datetime as dt

from src.collectors.table_access import TableAccessCollector
from tests.conftest import load_fixture


def _rows_with_db_tag():
    return load_fixture("table_access_usage_stats.json")


def test_transform_maps_columns_and_stamps_source_instance_and_date(monkeypatch):
    fixed_date = dt.date(2026, 7, 1)
    monkeypatch.setattr(TableAccessCollector, "_today", staticmethod(lambda: fixed_date))

    collector = TableAccessCollector(source_instance="PROD-SQL-01", config={})
    out = collector.transform(_rows_with_db_tag())

    assert out[0] == {
        "source_instance": "PROD-SQL-01",
        "snapshot_date": fixed_date,
        "database_name": "AppDb",
        "schema_name": "dbo",
        "table_name": "Orders",
        "seeks_cum": 500000,
        "scans_cum": 1200,
        "lookups_cum": 30000,
        "updates_cum": 45000,
        "last_user_read_utc": "2026-06-30T23:59:00",
        "last_user_update_utc": "2026-06-30T23:58:00",
    }


def test_columns_match_upsert_placeholder_order():
    collector = TableAccessCollector(source_instance="PROD-SQL-01", config={})
    assert collector.columns() == (
        "source_instance",
        "snapshot_date",
        "database_name",
        "schema_name",
        "table_name",
        "seeks_cum",
        "scans_cum",
        "lookups_cum",
        "updates_cum",
        "last_user_read_utc",
        "last_user_update_utc",
    )
    sql = collector.upsert_sql()
    assert sql.count("?") == len(collector.columns())
    assert "MERGE dbo.fact_table_usage" in sql


def test_source_query_rolls_up_all_indexes_to_table_grain():
    collector = TableAccessCollector(source_instance="PROD-SQL-01", config={})
    query = collector.source_query()
    assert "sys.dm_db_index_usage_stats" in query
    assert "o.type = 'U'" in query
    assert "o.is_ms_shipped = 0" in query
    assert "GROUP BY s.name, o.name" in query
