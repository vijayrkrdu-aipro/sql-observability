"""Connection factory + collection_run logging.

Import-isolated (Section 7): pyodbc is imported lazily, only inside connect(), so this
module — and everything that imports it (collectors, run.py) — works on a machine with
no ODBC driver installed. Tests inject a fake connection instead of calling connect().
"""
from __future__ import annotations

import os
from typing import Any


def get_credentials_from_env(env_prefix: str) -> tuple[str, str] | None:
    """Read <PREFIX>_USER / <PREFIX>_PASSWORD from the environment (see .env.example)."""
    user = os.environ.get(f"{env_prefix}_USER")
    password = os.environ.get(f"{env_prefix}_PASSWORD")
    if user and password:
        return user, password
    return None


def build_connection_string(instance_config: dict[str, Any], credentials: tuple[str, str] | None = None) -> str:
    driver = instance_config["driver"]
    server = instance_config.get("server") or instance_config["name"]
    database = instance_config.get("database", "master")
    parts = [
        f"DRIVER={{{driver}}}",
        f"SERVER={server}",
        f"DATABASE={database}",
        f"Encrypt={'yes' if instance_config.get('encrypt', True) else 'no'}",
        f"TrustServerCertificate={'yes' if instance_config.get('trust_server_certificate', False) else 'no'}",
    ]
    if instance_config.get("integrated_auth", True):
        parts.append("Trusted_Connection=yes")
    else:
        if credentials is None:
            raise ValueError("SQL auth (integrated_auth: false) requires credentials, none provided")
        user, password = credentials
        parts.append(f"UID={user}")
        parts.append(f"PWD={password}")
    return ";".join(parts)


def connect(instance_config: dict[str, Any], credentials: tuple[str, str] | None = None):
    """Open a real pyodbc connection. Never called from tests (Section 1: no live DB here)."""
    import pyodbc  # noqa: PLC0415 - intentionally lazy, see module docstring

    return pyodbc.connect(build_connection_string(instance_config, credentials))


def execute(conn: Any, query: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Run a query and return rows as list[dict] keyed by column name."""
    cursor = conn.cursor()
    if params:
        cursor.execute(query, params)
    else:
        cursor.execute(query)
    if cursor.description is None:
        return []
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def start_run(conn: Any, source_instance: str, task: str) -> int:
    """Insert a 'running' collection_run row (guardrail #5); return its run_id."""
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO dbo.collection_run (source_instance, task, status) "
        "OUTPUT INSERTED.run_id VALUES (?, ?, 'running')",
        (source_instance, task),
    )
    run_id = cursor.fetchone()[0]
    conn.commit()
    return run_id


def finish_run(
    conn: Any,
    run_id: int,
    status: str,
    row_count: int | None = None,
    error_message: str | None = None,
) -> None:
    """Mark a collection_run row 'success' or 'failed' (Section 8: errors caught per task)."""
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE dbo.collection_run "
        "SET ended_at_utc = SYSUTCDATETIME(), status = ?, row_count = ?, error_message = ? "
        "WHERE run_id = ?",
        (status, row_count, error_message, run_id),
    )
    conn.commit()
