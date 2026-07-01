"""Exercises the base.Collector contract with a minimal sample collector (Task 1.3
acceptance: a sample collector runs --dry-run against the fake with zero writes).
Real collectors (cpu, waits, query_perf, ...) land in Task 1.4 with their own tests.
"""
import pytest

from src.collectors.base import Collector
from tests.conftest import FakeConnection


class SampleCollector(Collector):
    """Doubles every `value` column from a fake DMV row -> fake repo row."""

    task_name = "sample"

    def source_query(self) -> str:
        return "SELECT id, value FROM sys.dm_fake_thing"

    def transform(self, rows):
        return [{"id": r["id"], "doubled": r["value"] * 2} for r in rows]

    def upsert_sql(self) -> str:
        return "MERGE dbo.fact_sample AS t USING (SELECT ? AS id, ? AS doubled) AS s ..."

    def columns(self):
        return ("id", "doubled")


@pytest.fixture
def source_conn():
    return FakeConnection(rows=[(1, 10), (2, 20)], columns=["id", "value"])


@pytest.fixture
def collector():
    return SampleCollector(source_instance="PROD-SQL-01", config={})


def test_dry_run_performs_zero_writes(collector, source_conn):
    repo_conn = FakeConnection()
    row_count = collector.run(source_conn, repo_conn, dry_run=True)

    assert row_count == 2
    assert repo_conn.cursor_obj.executed == []  # no start_run/upsert/finish_run
    assert repo_conn.committed is False


def test_real_run_persists_and_logs_success(collector, source_conn):
    repo_conn = FakeConnection(scalar=99)
    row_count = collector.run(source_conn, repo_conn, dry_run=False)

    assert row_count == 2
    queries = [q for q, _ in repo_conn.cursor_obj.executed]
    assert any("INSERT INTO dbo.collection_run" in q for q in queries)
    assert sum("MERGE dbo.fact_sample" in q for q in queries) == 2  # one per row
    assert any("UPDATE dbo.collection_run" in q for q in queries)
    assert repo_conn.committed is True

    # transformed values reached the upsert with the right column order
    merge_calls = [(q, p) for q, p in repo_conn.cursor_obj.executed if "MERGE dbo.fact_sample" in q]
    assert merge_calls[0][1] == (1, 20)
    assert merge_calls[1][1] == (2, 40)


def test_real_run_marks_failed_on_persist_error(collector, source_conn, monkeypatch):
    repo_conn = FakeConnection(scalar=99)

    def boom(*_args, **_kwargs):
        raise RuntimeError("upsert exploded")

    monkeypatch.setattr(collector, "_persist", boom)

    with pytest.raises(RuntimeError, match="upsert exploded"):
        collector.run(source_conn, repo_conn, dry_run=False)

    query, params = repo_conn.cursor_obj.executed[-1]
    assert "UPDATE dbo.collection_run" in query
    assert params[0] == "failed"
    assert "upsert exploded" in params[2]
