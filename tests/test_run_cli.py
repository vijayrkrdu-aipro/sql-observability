"""Smoke tests for the run.py CLI shell (no DB/driver required)."""
import subprocess
import sys


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
