from src.collectors.cpu import CpuCollector
from tests.conftest import load_fixture


def _rows():
    fixture = load_fixture("cpu_ring_buffer.json")
    return [(r["sample_time_utc"], r["sql_cpu_pct"], r["idle_pct"]) for r in fixture]


def _dict_rows():
    columns = ["sample_time_utc", "sql_cpu_pct", "idle_pct"]
    return [dict(zip(columns, row, strict=True)) for row in _rows()]


def test_transform_computes_other_cpu_pct():
    collector = CpuCollector(source_instance="PROD-SQL-01", config={})
    out = collector.transform(_dict_rows())

    assert out[0] == {
        "source_instance": "PROD-SQL-01",
        "sample_time_utc": "2026-06-30T23:58:00",
        "sql_cpu_pct": 40,
        "other_cpu_pct": 5,  # 100 - 40 - 55
        "idle_pct": 55,
    }
    assert out[1]["other_cpu_pct"] == 5  # 100 - 90 - 5


def test_transform_clamps_other_cpu_pct_to_zero():
    collector = CpuCollector(source_instance="PROD-SQL-01", config={})
    out = collector.transform(_dict_rows())

    # sql=60 + idle=60 = 120 > 100 -> would be negative without the clamp
    assert out[2]["other_cpu_pct"] == 0


def test_columns_match_upsert_placeholder_order():
    collector = CpuCollector(source_instance="PROD-SQL-01", config={})
    assert collector.columns() == (
        "source_instance",
        "sample_time_utc",
        "sql_cpu_pct",
        "other_cpu_pct",
        "idle_pct",
    )
    sql = collector.upsert_sql()
    assert sql.count("?") == len(collector.columns())
    assert "MERGE dbo.fact_cpu" in sql
    assert "ON tgt.source_instance = src.source_instance AND tgt.sample_time_utc = src.sample_time_utc" in sql


def test_source_query_reads_ring_buffer():
    collector = CpuCollector(source_instance="PROD-SQL-01", config={})
    query = collector.source_query()
    assert "RING_BUFFER_SCHEDULER_MONITOR" in query
    assert "sys.dm_os_ring_buffers" in query
