import datetime as dt

from src.collectors.index_ops import IndexOpsCollector
from tests.conftest import load_fixture


def _rows_with_db_tag():
    return load_fixture("index_ops_dmv.json")


def test_transform_maps_missing_row(monkeypatch):
    fixed_date = dt.date(2026, 7, 1)
    monkeypatch.setattr(IndexOpsCollector, "_today", staticmethod(lambda: fixed_date))

    collector = IndexOpsCollector(source_instance="PROD-SQL-01", config={})
    out = collector.transform(_rows_with_db_tag())

    missing = next(r for r in out if r["kind"] == "missing")
    assert missing["object_name"] == "dbo.Orders"
    assert missing["impact_score"] == 8500.5
    assert missing["reads"] is None
    assert missing["writes"] is None
    assert "CustomerId" in missing["detail"]


def test_transform_maps_unused_row():
    collector = IndexOpsCollector(source_instance="PROD-SQL-01", config={})
    out = collector.transform(_rows_with_db_tag())

    unused = next(r for r in out if r["kind"] == "unused")
    assert unused["object_name"] == "dbo.Customers.IX_Customers_Old"
    assert unused["impact_score"] is None
    assert unused["reads"] == 0
    assert unused["writes"] == 1500
    assert unused["detail"] is None


def test_columns_match_upsert_placeholder_order():
    collector = IndexOpsCollector(source_instance="PROD-SQL-01", config={})
    assert collector.columns() == (
        "source_instance",
        "snapshot_date",
        "database_name",
        "kind",
        "object_name",
        "impact_score",
        "reads",
        "writes",
        "detail",
    )
    sql = collector.upsert_sql()
    assert sql.count("?") == len(collector.columns())
    assert "MERGE dbo.fact_index_ops" in sql


def test_source_query_unions_missing_and_unused_pulls():
    collector = IndexOpsCollector(source_instance="PROD-SQL-01", config={})
    query = collector.source_query()
    assert "sys.dm_db_missing_index_details" in query
    assert "sys.dm_db_index_usage_stats" in query
    assert "UNION ALL" in query
    assert "'missing' AS kind" in query
    assert "'unused' AS kind" in query
    assert "= 0" in query  # unused candidate: seeks+scans+lookups = 0
    assert "user_updates > 0" in query  # ... and writes > 0
