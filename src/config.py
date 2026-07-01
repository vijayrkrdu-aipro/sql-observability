"""Load and validate config.yaml. No secrets live here (see .env.example / db.py)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REQUIRED_TOP_LEVEL_KEYS = ("repository", "monitored_instances", "tasks")
REQUIRED_REPOSITORY_KEYS = ("server", "database", "driver", "encrypt", "trust_server_certificate", "integrated_auth")
REQUIRED_INSTANCE_KEYS = ("name", "driver", "encrypt", "trust_server_certificate", "integrated_auth", "databases")
REQUIRED_TASK_KEYS = ("enabled", "cadence_minutes")


class ConfigError(ValueError):
    """Raised when config.yaml is missing or structurally invalid."""


def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"config file not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ConfigError(f"config file is empty or not a mapping: {config_path}")
    validate_config(data)
    return data


def validate_config(config: dict[str, Any]) -> None:
    missing = [k for k in REQUIRED_TOP_LEVEL_KEYS if k not in config]
    if missing:
        raise ConfigError(f"config missing required top-level key(s): {missing}")

    _validate_repository(config["repository"])
    _validate_monitored_instances(config["monitored_instances"])
    _validate_tasks(config["tasks"])


def _validate_repository(repository: Any) -> None:
    if not isinstance(repository, dict):
        raise ConfigError("'repository' must be a mapping")
    missing = [k for k in REQUIRED_REPOSITORY_KEYS if k not in repository]
    if missing:
        raise ConfigError(f"'repository' missing required key(s): {missing}")


def _validate_monitored_instances(instances: Any) -> None:
    if not isinstance(instances, list) or not instances:
        raise ConfigError("'monitored_instances' must be a non-empty list")
    names = []
    for i, instance in enumerate(instances):
        if not isinstance(instance, dict):
            raise ConfigError(f"monitored_instances[{i}] must be a mapping")
        missing = [k for k in REQUIRED_INSTANCE_KEYS if k not in instance]
        if missing:
            raise ConfigError(f"monitored_instances[{i}] missing required key(s): {missing}")
        names.append(instance["name"])
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        raise ConfigError(f"duplicate monitored_instances name(s): {duplicates}")


def _validate_tasks(tasks: Any) -> None:
    if not isinstance(tasks, dict) or not tasks:
        raise ConfigError("'tasks' must be a non-empty mapping")
    for name, spec in tasks.items():
        if not isinstance(spec, dict):
            raise ConfigError(f"tasks.{name} must be a mapping")
        missing = [k for k in REQUIRED_TASK_KEYS if k not in spec]
        if missing:
            raise ConfigError(f"tasks.{name} missing required key(s): {missing}")


def env_var_prefix(instance_name: str) -> str:
    """Instance name -> env var prefix, e.g. 'PROD-SQL-01' -> 'PROD_SQL_01' (Section 4 / .env.example)."""
    return re.sub(r"[^A-Za-z0-9]", "_", instance_name).upper()
