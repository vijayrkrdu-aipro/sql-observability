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

    def run(self, source_conn: Any, repo_conn: Any, dry_run: bool = False) -> int:
        rows = db.execute(source_conn, self.source_query())
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
