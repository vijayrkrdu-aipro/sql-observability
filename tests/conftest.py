"""Shared offline test fakes (Section 13). No real DB/driver anywhere in this file."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    """Load a canned DMV rowset from tests/fixtures/<name>.json."""
    with (FIXTURES_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


class FakeCursor:
    """Stand-in for a pyodbc cursor.

    `rows`/`columns` back fetchall()/description for SELECT-style calls.
    `scalar` backs fetchone() for the `OUTPUT INSERTED.run_id` pattern in db.start_run.
    Every execute() call (query, params) is recorded in `executed` for assertions.
    """

    def __init__(
        self,
        rows: list[tuple] | None = None,
        columns: list[str] | None = None,
        scalar: Any = None,
    ):
        self._rows = rows or []
        self.description = [(c,) for c in columns] if columns else None
        self._scalar = scalar
        self.executed: list[tuple[str, tuple | None]] = []

    def execute(self, query: str, params: tuple | None = None) -> "FakeCursor":
        self.executed.append((query, params))
        return self

    def fetchall(self) -> list[tuple]:
        return self._rows

    def fetchone(self) -> tuple | None:
        return (self._scalar,) if self._scalar is not None else None


class FakeConnection:
    """Stand-in for a pyodbc connection; one FakeCursor shared across cursor() calls."""

    def __init__(
        self,
        rows: list[tuple] | None = None,
        columns: list[str] | None = None,
        scalar: Any = None,
    ):
        self.cursor_obj = FakeCursor(rows=rows, columns=columns, scalar=scalar)
        self.committed = False
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self.cursor_obj

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_connection():
    """Factory fixture: fake_connection(rows=..., columns=..., scalar=...) -> FakeConnection."""

    def _make(rows: list[tuple] | None = None, columns: list[str] | None = None, scalar: Any = None) -> FakeConnection:
        return FakeConnection(rows=rows, columns=columns, scalar=scalar)

    return _make
