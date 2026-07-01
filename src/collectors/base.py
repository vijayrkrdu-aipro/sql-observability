"""Collector contract (Section 8 of CLAUDE.md).

Subclasses implement source_query()/transform()/columns()/upsert_sql(); this base class
handles collection_run logging, dry-run, and error capture so every collector behaves the
same way around those concerns.
"""
from __future__ import annotations

import traceback
from abc import ABC, abstractmethod
from typing import Any

from src import db


class Collector(ABC):
    task_name: str

    def __init__(self, source_instance: str, config: dict[str, Any]):
        self.source_instance = source_instance
        self.config = config

    @abstractmethod
    def source_query(self) -> str:
        """SQL run read-only against the monitored instance."""

    @abstractmethod
    def transform(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Shape monitored-instance rows into repo rows (dicts keyed by columns())."""

    @abstractmethod
    def upsert_sql(self) -> str:
        """MERGE/insert statement with `?` placeholders in columns() order."""

    @abstractmethod
    def columns(self) -> tuple[str, ...]:
        """Column order matching upsert_sql()'s positional placeholders."""

    def fetch_rows(self, source_conn: Any) -> list[dict[str, Any]]:
        """Read from the monitored instance. Default: run source_query() once.

        PerDatabaseCollector overrides this to loop source_query() per target database
        (Section 11: storage/index_ops/table_access are current-database-scoped DMVs).
        """
        return db.execute(source_conn, self.source_query())

    def run(self, source_conn: Any, repo_conn: Any, dry_run: bool = False) -> int:
        rows = self.fetch_rows(source_conn)
        transformed = self.transform(rows)
        row_count = len(transformed)

        if dry_run:
            return row_count

        run_id = db.start_run(repo_conn, self.source_instance, self.task_name)
        try:
            self._persist(repo_conn, transformed)
        except Exception as exc:
            db.finish_run(repo_conn, run_id, "failed", None, f"{exc}\n{traceback.format_exc()}")
            raise
        else:
            db.finish_run(repo_conn, run_id, "success", row_count)
            return row_count

    def _persist(self, repo_conn: Any, rows: list[dict[str, Any]]) -> None:
        sql = self.upsert_sql()
        cols = self.columns()
        cursor = repo_conn.cursor()
        for row in rows:
            cursor.execute(sql, tuple(row[c] for c in cols))
        repo_conn.commit()


class PerDatabaseCollector(Collector):
    """Base for daily collectors whose DMVs are scoped to the CURRENT database
    (storage, index_ops, table_access - Section 11). Loops the monitored instance's
    configured `databases` list (config.yaml `monitored_instances[].databases`), or
    discovers all online user databases when that list is empty, running
    `USE [db]; <source_query()>` once per database and tagging each row with
    `database_name` before transform() sees them.
    """

    _DISCOVER_DATABASES_SQL = "SELECT name FROM sys.databases WHERE state_desc = 'ONLINE' AND database_id > 4;"

    def _configured_databases(self) -> list[str] | None:
        for instance in self.config.get("monitored_instances", []):
            if instance.get("name") == self.source_instance:
                databases = instance.get("databases") or []
                return list(databases) if databases else None
        return None

    def _discover_databases(self, source_conn: Any) -> list[str]:
        rows = db.execute(source_conn, self._DISCOVER_DATABASES_SQL)
        return [r["name"] for r in rows]

    def target_databases(self, source_conn: Any) -> list[str]:
        configured = self._configured_databases()
        return configured if configured is not None else self._discover_databases(source_conn)

    def fetch_rows(self, source_conn: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for database_name in self.target_databases(source_conn):
            escaped = database_name.replace("]", "]]")
            db_rows = db.execute(source_conn, f"USE [{escaped}];\n{self.source_query()}")
            for r in db_rows:
                r["database_name"] = database_name
            rows.extend(db_rows)
        return rows
