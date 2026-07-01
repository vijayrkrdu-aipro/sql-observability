"""CLI entrypoint: python run.py --task <name> [--dry-run] [--config config.yaml]

Exit 0 on success, non-zero on failure. See CLAUDE.md Section 8 for the contract.
"""
from __future__ import annotations

import argparse
import sys

# Task registry is populated as collectors land (Phase 1.4 / Phase 2.1-2.2).
# Keys must match config.yaml `tasks:`.
TASK_NAMES = [
    "cpu",
    "waits",
    "query_perf",
    "workload",
    "sessions",
    "concurrency",
    "storage",
    "index_ops",
    "table_access",
    "health",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="SQL Server Fleet Observability - collector runner",
    )
    parser.add_argument(
        "--task",
        required=True,
        choices=TASK_NAMES,
        help="Collector task to run",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the transform and print rowcounts; perform no writes (no DB required)",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Collector wiring (config load, connection factory, base.Collector dispatch)
    # lands in Phase 1.2-1.4. Scaffold stage only proves the CLI shell works.
    print(f"[run.py] task={args.task} dry_run={args.dry_run} config={args.config}")
    print(f"[run.py] collector '{args.task}' not yet implemented (see CLAUDE.md build plan)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
