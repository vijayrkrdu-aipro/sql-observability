"""CLI entrypoint: python run.py --task <name> [--dry-run] [--config config.yaml]

Exit 0 on success, non-zero on failure. See CLAUDE.md Section 8 for the contract.
"""
from __future__ import annotations

import argparse
import sys

from src import db
from src.collectors.base import Collector
from src.collectors.blocking import BlockingCollector
from src.collectors.concurrency import ConcurrencyCollector
from src.collectors.cpu import CpuCollector
from src.collectors.deadlocks import DeadlocksCollector
from src.collectors.health import HealthCollector
from src.collectors.index_ops import IndexOpsCollector
from src.collectors.io_latency import IoLatencyCollector
from src.collectors.query_perf import QueryPerfCollector
from src.collectors.sessions import SessionsCollector
from src.collectors.storage import StorageCollector
from src.collectors.table_access import TableAccessCollector
from src.collectors.waits import WaitsCollector
from src.collectors.workload import WorkloadCollector
from src.config import env_var_prefix, load_config

# Keys must match config.yaml `tasks:`. --task's choices are derived directly from this registry.
TASK_REGISTRY: dict[str, type[Collector]] = {
    "cpu": CpuCollector,
    "waits": WaitsCollector,
    "query_perf": QueryPerfCollector,
    "storage": StorageCollector,
    "index_ops": IndexOpsCollector,
    "table_access": TableAccessCollector,
    "health": HealthCollector,
    "workload": WorkloadCollector,
    "sessions": SessionsCollector,
    "concurrency": ConcurrencyCollector,
    "io_latency": IoLatencyCollector,
    "blocking": BlockingCollector,
    "deadlocks": DeadlocksCollector,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="SQL Server Fleet Observability - collector runner",
    )
    parser.add_argument(
        "--task",
        required=True,
        choices=sorted(TASK_REGISTRY),
        help="Collector task to run",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the transform and print rowcounts; perform no writes (still reads the source)",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    return parser


def _connect(instance_config: dict, env_prefix: str):
    credentials = None
    if not instance_config.get("integrated_auth", True):
        credentials = db.get_credentials_from_env(env_prefix)
    return db.connect(instance_config, credentials)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    collector_cls = TASK_REGISTRY[args.task]

    config = load_config(args.config)
    exit_code = 0

    for instance in config["monitored_instances"]:
        source_instance = instance["name"]
        collector = collector_cls(source_instance=source_instance, config=config)
        source_conn = repo_conn = None
        try:
            source_conn = _connect(instance, env_var_prefix(source_instance))
            repo_conn = _connect(config["repository"], "REPO_SQL")
            row_count = collector.run(source_conn, repo_conn, dry_run=args.dry_run)
            print(
                f"[run.py] task={args.task} source_instance={source_instance} "
                f"rows={row_count} dry_run={args.dry_run}"
            )
        except Exception as exc:  # noqa: BLE001 - one failing instance must not block others
            print(f"[run.py] task={args.task} source_instance={source_instance} FAILED: {exc}", file=sys.stderr)
            exit_code = 1
        finally:
            for conn in (source_conn, repo_conn):
                if conn is not None:
                    conn.close()

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
