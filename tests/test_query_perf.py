from src.collectors.query_perf import QueryPerfCollector
from tests.conftest import load_fixture

CONFIG = {"query_perf": {"top_n": 50}}

_COLUMNS = [
    "query_hash",
    "exec_count",
    "avg_cpu_ms",
    "avg_duration_ms",
    "avg_logical_reads",
    "total_cpu_ms",
    "query_sql_text",
]


def _dict_rows():
    fixture = load_fixture("query_perf_plan_cache.json")
    rows = []
    for r in fixture:
        row = (
            bytes.fromhex(r["query_hash_hex"]),
            r["exec_count"],
            r["avg_cpu_ms"],
            r["avg_duration_ms"],
            r["avg_logical_reads"],
            r["total_cpu_ms"],
            r["query_sql_text"],
        )
        rows.append(dict(zip(_COLUMNS, row, strict=True)))
    return rows


def test_transform_collapses_duplicate_query_hash_to_one_row():
    collector = QueryPerfCollector(source_instance="PROD-SQL-01", config=CONFIG)
    out = collector.transform(_dict_rows())

    hashes = [row["query_hash"] for row in out]
    assert len(hashes) == len(set(hashes))
    assert len(out) == 2  # 3 input rows, 2 distinct query_hash


def test_transform_handles_null_query_text_for_evicted_plan():
    collector = QueryPerfCollector(source_instance="PROD-SQL-01", config=CONFIG)
    out = collector.transform(_dict_rows())

    evicted = next(r for r in out if r["query_hash"] == bytes.fromhex("aabbccddeeff0011"))
    assert evicted["query_sql_text"] is None


def test_transform_stamps_source_instance_and_snapshot_time():
    collector = QueryPerfCollector(source_instance="PROD-SQL-01", config=CONFIG)
    out = collector.transform(_dict_rows())

    assert all(row["source_instance"] == "PROD-SQL-01" for row in out)
    snapshot_times = {row["snapshot_time_utc"] for row in out}
    assert len(snapshot_times) == 1  # one snapshot per run


def test_source_query_uses_configured_top_n_and_converts_microseconds_to_ms():
    collector = QueryPerfCollector(source_instance="PROD-SQL-01", config={"query_perf": {"top_n": 25}})
    query = collector.source_query()

    assert query.count("TOP (25)") == 2
    assert "sys.dm_exec_query_stats" in query
    assert "GROUP BY qs.query_hash" in query
    assert "/ 1000.0" in query  # µs -> ms conversion for cpu/duration
    assert "ORDER BY total_cpu_ms DESC" in query
    assert "ORDER BY avg_logical_reads DESC" in query
    assert "UNION" in query


def test_source_query_defaults_top_n_to_50_when_unconfigured():
    collector = QueryPerfCollector(source_instance="PROD-SQL-01", config={})
    assert collector.source_query().count("TOP (50)") == 2


def test_columns_match_upsert_placeholder_order():
    collector = QueryPerfCollector(source_instance="PROD-SQL-01", config=CONFIG)
    assert collector.columns() == (
        "source_instance",
        "snapshot_time_utc",
        "query_hash",
        "exec_count",
        "avg_cpu_ms",
        "avg_duration_ms",
        "avg_logical_reads",
        "total_cpu_ms",
        "query_sql_text",
    )
    sql = collector.upsert_sql()
    assert sql.count("?") == len(collector.columns())
    assert "MERGE dbo.fact_query_perf" in sql
