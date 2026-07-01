"""Exercises PerDatabaseCollector's database-looping seam (Task 2.1), used by
storage.py/index_ops.py/table_access.py. Uses a query-aware fake connection since these
tests need different canned results for the discovery query vs. the per-database query.
"""
from src.collectors.base import PerDatabaseCollector


class QueryAwareFakeCursor:
    """Returns canned (rows, columns) keyed by a substring match against the query text."""

    def __init__(self, responses: dict[str, tuple[list[tuple], list[str]]]):
        self.responses = responses
        self.executed: list[tuple[str, tuple | None]] = []
        self._rows: list[tuple] = []
        self.description = None

    def execute(self, query: str, params: tuple | None = None):
        self.executed.append((query, params))
        for key, (rows, columns) in self.responses.items():
            if key in query:
                self._rows = rows
                self.description = [(c,) for c in columns]
                return self
        self._rows, self.description = [], None
        return self

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return None


class QueryAwareFakeConnection:
    def __init__(self, responses):
        self.cursor_obj = QueryAwareFakeCursor(responses)

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        pass

    def close(self):
        pass


class SamplePerDbCollector(PerDatabaseCollector):
    task_name = "sample_per_db"

    def source_query(self) -> str:
        return "SELECT val FROM sys.dm_fake_per_db"

    def transform(self, rows):
        return rows  # passthrough; these tests only care about fetch_rows()

    def upsert_sql(self) -> str:
        return "MERGE dbo.fact_sample_per_db ..."

    def columns(self):
        return ("database_name", "val")


def test_fetch_rows_discovers_databases_when_config_list_is_empty():
    responses = {
        "sys.databases": ([("AppDb",), ("Reporting",)], ["name"]),
        "sys.dm_fake_per_db": ([(1,)], ["val"]),
    }
    conn = QueryAwareFakeConnection(responses)
    config = {"monitored_instances": [{"name": "PROD-SQL-01", "databases": []}]}
    collector = SamplePerDbCollector(source_instance="PROD-SQL-01", config=config)

    rows = collector.fetch_rows(conn)

    assert {r["database_name"] for r in rows} == {"AppDb", "Reporting"}
    executed_queries = [q for q, _ in conn.cursor_obj.executed]
    assert executed_queries[0] == PerDatabaseCollector._DISCOVER_DATABASES_SQL
    assert "USE [AppDb];" in executed_queries[1]
    assert "USE [Reporting];" in executed_queries[2]


def test_fetch_rows_uses_configured_databases_without_discovery():
    responses = {"sys.dm_fake_per_db": ([(1,)], ["val"])}
    conn = QueryAwareFakeConnection(responses)
    config = {"monitored_instances": [{"name": "PROD-SQL-01", "databases": ["OnlyThisDb"]}]}
    collector = SamplePerDbCollector(source_instance="PROD-SQL-01", config=config)

    rows = collector.fetch_rows(conn)

    assert [r["database_name"] for r in rows] == ["OnlyThisDb"]
    executed_queries = [q for q, _ in conn.cursor_obj.executed]
    assert len(executed_queries) == 1  # no discovery query issued
    assert "USE [OnlyThisDb];" in executed_queries[0]


def test_fetch_rows_escapes_closing_bracket_in_database_name():
    responses = {"sys.dm_fake_per_db": ([], ["val"])}
    conn = QueryAwareFakeConnection(responses)
    config = {"monitored_instances": [{"name": "PROD-SQL-01", "databases": ["Weird]Db"]}]}
    collector = SamplePerDbCollector(source_instance="PROD-SQL-01", config=config)

    collector.fetch_rows(conn)

    query, _ = conn.cursor_obj.executed[0]
    assert "USE [Weird]]Db];" in query
