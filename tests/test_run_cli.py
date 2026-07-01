"""Smoke tests for the run.py CLI shell and TASK_REGISTRY dispatch (no DB/driver required)."""
import subprocess
import sys

import run as run_module
from tests.conftest import FakeConnection

_CONFIG = {
    "repository": {
        "server": "REPO-SQL-01",
        "database": "DBA_Observability",
        "driver": "ODBC Driver 18 for SQL Server",
        "encrypt": True,
        "trust_server_certificate": False,
        "integrated_auth": True,
    },
    "monitored_instances": [
        {
            "name": "PROD-SQL-01",
            "driver": "ODBC Driver 18 for SQL Server",
            "encrypt": True,
            "trust_server_certificate": False,
            "integrated_auth": True,
            "databases": [],
        }
    ],
    "tasks": {"cpu": {"enabled": True, "cadence_minutes": 15}},
}


def test_help_exits_zero():
    result = subprocess.run(
        [sys.executable, "run.py", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--task" in result.stdout


def test_missing_task_exits_nonzero():
    result = subprocess.run(
        [sys.executable, "run.py"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


def test_unknown_task_rejected_by_argparse():
    result = subprocess.run(
        [sys.executable, "run.py", "--task", "not_a_real_task"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "invalid choice" in result.stderr


def test_registered_task_dispatches_collector_and_closes_connections(monkeypatch, capsys):
    monkeypatch.setattr(run_module, "load_config", lambda _path: _CONFIG)

    opened = []

    def fake_connect(_instance_config, _env_prefix):
        conn = FakeConnection(rows=[], columns=["sample_time_utc", "sql_cpu_pct", "idle_pct"])
        opened.append(conn)
        return conn

    monkeypatch.setattr(run_module, "_connect", fake_connect)

    exit_code = run_module.main(["--task", "cpu", "--dry-run"])

    assert exit_code == 0
    assert len(opened) == 2  # source + repo
    assert all(conn.closed for conn in opened)
    assert "rows=0 dry_run=True" in capsys.readouterr().out
