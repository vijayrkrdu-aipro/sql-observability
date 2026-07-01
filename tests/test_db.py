import sys

import pytest

from src import db

INSTANCE_INTEGRATED = {
    "name": "PROD-SQL-01",
    "driver": "ODBC Driver 18 for SQL Server",
    "encrypt": True,
    "trust_server_certificate": False,
    "integrated_auth": True,
}

INSTANCE_SQL_AUTH = {**INSTANCE_INTEGRATED, "integrated_auth": False}


class FakeCursor:
    """Minimal stand-in for a pyodbc cursor; formalized as a shared fixture in Task 1.3."""

    def __init__(self, rows=None, columns=None, scalar=None):
        self._rows = rows or []
        self.description = [(c,) for c in columns] if columns else None
        self._scalar = scalar
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return (self._scalar,)


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True


def test_module_imports_without_pyodbc_installed():
    assert "pyodbc" not in sys.modules  # never imported at module scope (Section 7)


def test_get_credentials_from_env_present(monkeypatch):
    monkeypatch.setenv("PROD_SQL_01_USER", "svc")
    monkeypatch.setenv("PROD_SQL_01_PASSWORD", "secret")
    assert db.get_credentials_from_env("PROD_SQL_01") == ("svc", "secret")


def test_get_credentials_from_env_missing(monkeypatch):
    monkeypatch.delenv("PROD_SQL_01_USER", raising=False)
    monkeypatch.delenv("PROD_SQL_01_PASSWORD", raising=False)
    assert db.get_credentials_from_env("PROD_SQL_01") is None


def test_build_connection_string_integrated_auth():
    conn_str = db.build_connection_string(INSTANCE_INTEGRATED)
    assert "Trusted_Connection=yes" in conn_str
    assert "UID=" not in conn_str
    assert "SERVER=PROD-SQL-01" in conn_str


def test_build_connection_string_sql_auth_with_credentials():
    conn_str = db.build_connection_string(INSTANCE_SQL_AUTH, credentials=("svc", "pw"))
    assert "UID=svc" in conn_str
    assert "PWD=pw" in conn_str
    assert "Trusted_Connection" not in conn_str


def test_build_connection_string_sql_auth_without_credentials_raises():
    with pytest.raises(ValueError, match="credentials"):
        db.build_connection_string(INSTANCE_SQL_AUTH)


def test_execute_returns_list_of_dicts():
    cursor = FakeCursor(rows=[(1, "a"), (2, "b")], columns=["id", "name"])
    conn = FakeConnection(cursor)
    rows = db.execute(conn, "SELECT id, name FROM t")
    assert rows == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]


def test_execute_no_result_set_returns_empty_list():
    cursor = FakeCursor(rows=[], columns=None)
    conn = FakeConnection(cursor)
    assert db.execute(conn, "UPDATE t SET x = 1") == []


def test_start_run_returns_run_id_and_commits():
    cursor = FakeCursor(scalar=42)
    conn = FakeConnection(cursor)
    run_id = db.start_run(conn, "PROD-SQL-01", "cpu")
    assert run_id == 42
    assert conn.committed is True
    query, params = cursor.executed[0]
    assert "INSERT INTO dbo.collection_run" in query
    assert params == ("PROD-SQL-01", "cpu")


def test_finish_run_updates_status_and_commits():
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    db.finish_run(conn, run_id=42, status="success", row_count=100)
    assert conn.committed is True
    query, params = cursor.executed[0]
    assert "UPDATE dbo.collection_run" in query
    assert params == ("success", 100, None, 42)
