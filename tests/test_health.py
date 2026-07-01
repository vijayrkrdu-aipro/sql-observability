import datetime as dt

from src.collectors.health import HealthCollector
from tests.conftest import load_fixture


def _dict_rows():
    return load_fixture("health_dmv.json")


def test_transform_maps_columns_and_stamps_source_instance_and_date(monkeypatch):
    fixed_date = dt.date(2026, 7, 1)
    monkeypatch.setattr(HealthCollector, "_today", staticmethod(lambda: fixed_date))

    collector = HealthCollector(source_instance="PROD-SQL-01", config={})
    out = collector.transform(_dict_rows())

    assert out[0] == {
        "source_instance": "PROD-SQL-01",
        "snapshot_date": fixed_date,
        "database_name": "AppDb",
        "last_full_backup_utc": "2026-06-30T02:00:00",
        "last_log_backup_utc": "2026-07-01T05:45:00",
        "recovery_model": "FULL",
        "state_desc": "ONLINE",
        "job_failures_24h": 0,
    }


def test_transform_handles_null_backup_dates_for_never_backed_up_db(monkeypatch):
    fixed_date = dt.date(2026, 7, 1)
    monkeypatch.setattr(HealthCollector, "_today", staticmethod(lambda: fixed_date))

    collector = HealthCollector(source_instance="PROD-SQL-01", config={})
    out = collector.transform(_dict_rows())

    reporting = next(r for r in out if r["database_name"] == "Reporting")
    assert reporting["last_full_backup_utc"] is None
    assert reporting["last_log_backup_utc"] is None
    assert reporting["job_failures_24h"] == 2


def test_columns_match_upsert_placeholder_order():
    collector = HealthCollector(source_instance="PROD-SQL-01", config={})
    assert collector.columns() == (
        "source_instance",
        "snapshot_date",
        "database_name",
        "last_full_backup_utc",
        "last_log_backup_utc",
        "recovery_model",
        "state_desc",
        "job_failures_24h",
    )
    sql = collector.upsert_sql()
    assert sql.count("?") == len(collector.columns())
    assert "MERGE dbo.fact_health" in sql


def test_source_query_reads_backupset_and_job_history_from_msdb():
    collector = HealthCollector(source_instance="PROD-SQL-01", config={})
    query = collector.source_query()
    assert "msdb.dbo.backupset" in query
    assert "msdb.dbo.sysjobhistory" in query
    assert "sys.databases" in query
    assert "d.database_id > 4" in query  # excludes master/tempdb/model/msdb
