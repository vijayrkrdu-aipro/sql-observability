import datetime as dt

from src.collectors.sessions import SessionsCollector
from tests.conftest import load_fixture


def _dict_rows():
    return load_fixture("sessions_active_requests.json")


def test_transform_aggregates_by_login_program_host_db(monkeypatch):
    fixed_now = dt.datetime(2026, 7, 1, 0, 0, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(SessionsCollector, "_utcnow", staticmethod(lambda: fixed_now))

    collector = SessionsCollector(source_instance="PROD-SQL-01", config={})
    out = collector.transform(_dict_rows())

    assert len(out) == 2  # two distinct (login, program, host, db) groups

    app = next(r for r in out if r["login_name"] == "svc_app")
    assert app["active_requests"] == 2
    assert app["cpu_ms_inflight"] == 350  # 100 + 250
    assert app["reads_inflight"] == 13000  # 5000 + 8000
    assert app["sample_time_utc"] == fixed_now
    assert app["source_instance"] == "PROD-SQL-01"

    etl = next(r for r in out if r["login_name"] == "svc_etl")
    assert etl["active_requests"] == 1
    assert etl["cpu_ms_inflight"] == 4000
    assert etl["reads_inflight"] == 900000


def test_columns_match_upsert_placeholder_order():
    collector = SessionsCollector(source_instance="PROD-SQL-01", config={})
    assert collector.columns() == (
        "source_instance",
        "sample_time_utc",
        "login_name",
        "program_name",
        "host_name",
        "database_name",
        "active_requests",
        "cpu_ms_inflight",
        "reads_inflight",
    )
    sql = collector.upsert_sql()
    assert sql.count("?") == len(collector.columns())
    assert "MERGE dbo.fact_session_sample" in sql


def test_source_query_excludes_own_spid_and_system_processes():
    collector = SessionsCollector(source_instance="PROD-SQL-01", config={})
    query = collector.source_query()
    assert "sys.dm_exec_requests" in query
    assert "sys.dm_exec_sessions" in query
    assert "s.is_user_process = 1" in query
    assert "r.session_id <> @@SPID" in query
