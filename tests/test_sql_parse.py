"""Best-effort sqlglot parse smoke test over sql/*.sql (Section 13). T-SQL batches are
split on GO (sqlglot has no notion of GO); some DMV/XML-heavy T-SQL may not fully parse in
future files -- if that happens here, loosen this rather than block the build on it.
"""
import re
from pathlib import Path

import pytest
import sqlglot

SQL_DIR = Path(__file__).parent.parent / "sql"
SQL_FILES = sorted(SQL_DIR.glob("*.sql"))


def _batches(sql_text: str) -> list[str]:
    return [b.strip() for b in re.split(r"^\s*GO\s*$", sql_text, flags=re.MULTILINE) if b.strip()]


@pytest.mark.parametrize("sql_file", SQL_FILES, ids=lambda p: p.name)
def test_sql_file_batches_parse_as_tsql(sql_file):
    text = sql_file.read_text(encoding="utf-8")
    batches = _batches(text)
    assert batches, f"{sql_file.name} produced no GO-separated batches to parse"

    for i, batch in enumerate(batches):
        try:
            sqlglot.parse(batch, dialect="tsql")
        except Exception as exc:  # noqa: BLE001 - report which batch failed, not just that one did
            pytest.fail(f"{sql_file.name} batch {i} failed to parse: {exc}\n---\n{batch[:300]}")
