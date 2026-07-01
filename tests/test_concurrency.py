import datetime as dt

from src.collectors.concurrency import ConcurrencyCollector

_ROW = {
    "user_sessions": 42,
    "running": 3,
    "runnable": 1,
    "suspended": 5,
    "blocked": 2,
    "memory_grants_pending": 0,
    "longest_open_tran_sec": 120,
}


def test_transform_maps_single_row_and_stamps_sample_time(monkeypatch):
    fixed_now = dt.datetime(2026, 7, 1, 0, 1, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(ConcurrencyCollector, "_utcnow", staticmethod(lambda: fixed_now))

    collector = ConcurrencyCollector(source_instance="PROD-SQL-01", config={})
    out = collector.transform([_ROW])

    assert out == [
        {
            "source_instance": "PROD-SQL-01",
            "sample_time_utc": fixed_now,
            "user_sessions": 42,
            "running": 3,
            "runnable": 1,
            "suspended": 5,
            "blocked": 2,
            "memory_grants_pending": 0,
            "longest_open_tran_sec": 120,
        }
    ]


def test_columns_match_upsert_placeholder_order():
    collector = ConcurrencyCollector(source_instance="PROD-SQL-01", config={})
    assert collector.columns() == (
        "source_instance",
        "sample_time_utc",
        "user_sessions",
        "running",
        "runnable",
        "suspended",
        "blocked",
        "memory_grants_pending",
        "longest_open_tran_sec",
    )
    sql = collector.upsert_sql()
    assert sql.count("?") == len(collector.columns())
    assert "MERGE dbo.fact_concurrency" in sql


def test_source_query_matches_rt_concurrency_now_semantics():
    collector = ConcurrencyCollector(source_instance="PROD-SQL-01", config={})
    query = collector.source_query()
    assert "sys.dm_exec_sessions" in query
    assert "sys.dm_exec_requests" in query
    assert "sys.dm_exec_query_memory_grants" in query
    assert "sys.dm_tran_active_transactions" in query
    assert "AS runnable" in query  # aliased to fact_concurrency's column name, not rt.*'s
    assert "AS suspended" in query
